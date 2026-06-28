import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

import { getStatus, runSync } from "./github/src/service.js";
import { openDatabase } from "./github/src/sqlite.js";
import { existingStateDbPath } from "./github/src/sqlite-state.js";

function sumCounts(counts = {}) {
  return Object.values(counts).reduce((total, value) => total + Number(value || 0), 0);
}

function formatCounts(counts = {}) {
  const entries = Object.entries(counts);
  if (entries.length === 0) {
    return "无";
  }
  return entries.map(([provider, count]) => `${provider} ${count} 条`).join("，");
}

function countHidden(counts = {}, currentProvider) {
  return Object.entries(counts)
    .filter(([provider]) => provider !== currentProvider)
    .reduce((total, [, count]) => total + Number(count || 0), 0);
}

function cleanText(value, fallback = "未命名") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text || fallback;
}

function toUnixSeconds(value) {
  const ms = Date.parse(value || "");
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : Math.floor(Date.now() / 1000);
}

function toUnixMs(value) {
  const ms = Date.parse(value || "");
  return Number.isFinite(ms) ? ms : Date.now();
}

function charWidth(char) {
  return /[\u1100-\u115f\u2329\u232a\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\ufe19\ufe30-\ufe6f\uff00-\uff60\uffe0-\uffe6]/u.test(char)
    ? 2
    : 1;
}

function displayWidth(value) {
  return [...String(value ?? "")].reduce((total, char) => total + charWidth(char), 0);
}

function shorten(value, maxWidth) {
  const text = cleanText(value, "");
  if (displayWidth(text) <= maxWidth) {
    return text;
  }
  let result = "";
  let width = 0;
  for (const char of text) {
    const nextWidth = charWidth(char);
    if (width + nextWidth > maxWidth - 1) {
      break;
    }
    result += char;
    width += nextWidth;
  }
  return `${result}…`;
}

function padDisplay(value, width) {
  const text = String(value ?? "");
  const padding = Math.max(0, width - displayWidth(text));
  return `${text}${" ".repeat(padding)}`;
}

function formatProjectVisibility(projects = []) {
  if (projects.length === 0) {
    return "无项目可见性诊断";
  }
  return projects
    .map((project) => {
      const ranks = project.rankPreview || "无";
      return `${project.root}：交互 ${project.interactiveThreads} 条，首页 ${project.firstPageThreads}/50，排名 ${ranks}，精确路径 ${project.exactCwdMatches}/${project.interactiveThreads}`;
    })
    .join("\n  ");
}

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

function conversationBackupRoot(codexHome) {
  return path.join(codexHome, "backups_state", "py-provider-sync");
}

async function copyDirectory(sourceDir, targetDir) {
  await fs.mkdir(targetDir, { recursive: true });
  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  for (const entry of entries) {
    const sourcePath = path.join(sourceDir, entry.name);
    const targetPath = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      await copyDirectory(sourcePath, targetPath);
    } else if (entry.isFile()) {
      await fs.mkdir(path.dirname(targetPath), { recursive: true });
      await fs.copyFile(sourcePath, targetPath);
    }
  }
}

async function pruneConversationBackups(codexHome, keep = 3) {
  const backups = await listConversationBackups(codexHome);
  const toDelete = backups.slice(keep);
  for (const backup of toDelete) {
    await fs.rm(backup.fullPath, { recursive: true, force: true });
  }
  return {
    deleted: toDelete.length,
    remaining: backups.length - toDelete.length
  };
}

async function createConversationBackup(codexHome) {
  const sourceSessions = path.join(codexHome, "sessions");
  if (!await pathExists(sourceSessions)) {
    return {
      created: false,
      reason: `sessions 目录不存在：${sourceSessions}`
    };
  }

  const root = conversationBackupRoot(codexHome);
  const backupDir = path.join(root, timestampForPath());
  await copyDirectory(sourceSessions, path.join(backupDir, "sessions"));
  const pruneResult = await pruneConversationBackups(codexHome, 3);
  return {
    created: true,
    backupDir,
    deletedOldBackups: pruneResult.deleted,
    remainingBackups: pruneResult.remaining
  };
}

async function listConversationBackups(codexHome) {
  const root = conversationBackupRoot(codexHome);
  let entries;
  try {
    entries = await fs.readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }

  const backups = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const fullPath = path.join(root, entry.name);
    const sessionsPath = path.join(fullPath, "sessions");
    if (!await pathExists(sessionsPath)) {
      continue;
    }
    const files = await listRolloutFiles(sessionsPath);
    const stat = await fs.stat(fullPath);
    backups.push({
      name: entry.name,
      fullPath,
      sessionsPath,
      count: files.length,
      mtimeMs: stat.mtimeMs
    });
  }
  return backups.sort((left, right) => right.name.localeCompare(left.name));
}

async function readRolloutMeta(filePath) {
  const firstLine = await readFirstLine(filePath);
  const record = JSON.parse(firstLine);
  if (record?.type !== "session_meta" || !record.payload) {
    return null;
  }
  return {
    id: record.payload.id || record.payload.session_id || path.basename(filePath, ".jsonl"),
    timestamp: record.payload.timestamp || record.timestamp || "",
    sourcePath: filePath
  };
}

async function listBackupConversationFiles(backup) {
  const files = await listRolloutFiles(backup.sessionsPath);
  const result = [];
  for (const filePath of files) {
    try {
      const meta = await readRolloutMeta(filePath);
      if (meta) {
        result.push(meta);
      }
    } catch {
      // Skip malformed backup rollout files.
    }
  }
  return result.sort((left, right) => {
    const rightTime = Date.parse(right.timestamp || "");
    const leftTime = Date.parse(left.timestamp || "");
    return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0);
  });
}

function targetPathForBackupRollout(codexHome, backup, sourcePath) {
  const relativePath = path.relative(backup.sessionsPath, sourcePath);
  if (relativePath.startsWith("..") || path.isAbsolute(relativePath)) {
    throw new Error(`备份文件路径异常：${sourcePath}`);
  }
  return path.join(codexHome, "sessions", relativePath);
}

async function restoreConversationFiles(codexHome, backup, requestedCount) {
  const conversations = await listBackupConversationFiles(backup);
  const count = requestedCount >= conversations.length ? conversations.length : Math.max(0, requestedCount);
  const selected = conversations.slice(0, count);
  const indexNames = await readSessionIndexNames(codexHome);
  for (const item of selected) {
    const targetPath = targetPathForBackupRollout(codexHome, backup, item.sourcePath);
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    await fs.copyFile(item.sourcePath, targetPath);
  }
  return {
    restored: selected.length,
    available: conversations.length,
    items: selected.map((item) => ({
      id: item.id,
      name: cleanText(indexNames.get(item.id)?.name || item.id)
    }))
  };
}

async function deleteConversationBackups(codexHome) {
  const root = conversationBackupRoot(codexHome);
  const backups = await listConversationBackups(codexHome);
  if (backups.length === 0) {
    return { deleted: 0, root };
  }
  await fs.rm(root, { recursive: true, force: true });
  return { deleted: backups.length, root };
}

function parsePositiveInteger(value) {
  const text = String(value || "").trim();
  if (!/^\d+$/.test(text)) {
    return null;
  }
  const parsed = Number.parseInt(text, 10);
  return parsed > 0 ? parsed : null;
}

async function createPrompter() {
  const rl = readline.createInterface({ input, output });
  return {
    question: (prompt) => rl.question(prompt),
    close: () => rl.close()
  };
}

async function runRestoreFlow(codexHome, prompter) {
  const initialBackups = await listConversationBackups(codexHome);
  console.log(`可恢复对话文件备份：${initialBackups.length} 个`);
  if (initialBackups.length === 0) {
    console.log(`恢复选项不可用：${conversationBackupRoot(codexHome)} 下没有包含 sessions/ 的备份。`);
    return;
  }

  const deleteAnswer = await prompter.question("是否删除所有对话文件备份？是请输入 YES，不是任意键：");
  if (deleteAnswer.trim() === "YES") {
    const result = await deleteConversationBackups(codexHome);
    console.log(`已删除 ${result.deleted} 个对话文件备份：${result.root}`);
  }

  const backups = await listConversationBackups(codexHome);
  if (backups.length === 0) {
    console.log("没有可用于恢复的对话文件备份。");
    return;
  }

  let targetBackup = backups[0];
  const latestAnswer = await prompter.question("在备份中选择最新的备份进行恢复吗？是：Y，不是：N：");
  if (latestAnswer.trim().toUpperCase() !== "Y") {
    console.log("所有可恢复备份：");
    backups.forEach((backup, index) => {
      console.log(`  ${index + 1}. ${backup.name}（${backup.count} 条对话）`);
    });
    while (true) {
      const indexAnswer = await prompter.question("以第几个为恢复目标：");
      const index = parsePositiveInteger(indexAnswer);
      if (index && index <= backups.length) {
        targetBackup = backups[index - 1];
        break;
      }
      console.log(`请输入 1 到 ${backups.length} 之间的编号。`);
    }
  }

  const backupFiles = await listBackupConversationFiles(targetBackup);
  let requestedCount;
  while (true) {
    const countAnswer = await prompter.question(`想要恢复的对话数（该备份共有 ${backupFiles.length} 条）：`);
    requestedCount = parsePositiveInteger(countAnswer);
    if (requestedCount !== null) {
      break;
    }
    console.log("请输入正整数。");
  }

  const actualCount = requestedCount >= backupFiles.length ? backupFiles.length : requestedCount;
  console.log(`将从备份 ${targetBackup.name} 恢复最近 ${actualCount} 条对话。`);
  const confirm = await prompter.question("确认恢复吗？Y：确认；N：退出：");
  if (confirm.trim().toUpperCase() !== "Y") {
    console.log("已退出，未恢复任何内容。");
    return;
  }

  const result = await restoreConversationFiles(codexHome, targetBackup, requestedCount);
  console.log(`恢复完成：${result.restored}/${result.available} 条。`);
  if (result.items.length > 0) {
    console.log("恢复 ID 列表：");
    result.items.forEach((item, index) => {
      const idLabel = padDisplay(`${index + 1}. ID：${item.id}`, 50);
      console.log(`  ${idLabel}Desktop 命名：${shorten(item.name, 50)}`);
    });
  }
}

async function interactive(codexHome) {
  const backupResult = await createConversationBackup(codexHome);
  if (backupResult.created) {
    console.log(`启动自动备份：${backupResult.backupDir}`);
    console.log(`备份保留：${backupResult.remainingBackups} 份；本次清理旧备份：${backupResult.deletedOldBackups} 份`);
    console.log("");
  } else {
    console.log(`启动自动备份跳过：${backupResult.reason}`);
    console.log("");
  }

  await printStatus(codexHome);

  const prompter = await createPrompter();
  try {
    console.log("");
    console.log("选择：");
    const firstAnswer = await prompter.question("输入 B：将未入库的对话入库；其它键继续：");
    const first = firstAnswer.trim().toUpperCase();
    if (first === "B") {
      await backfill(codexHome);
      return;
    }

    await runRestoreFlow(codexHome, prompter);

    const finalAnswer = await prompter.question("输入 Y 进行保存；其它键取消：");
    if (finalAnswer.trim().toUpperCase() === "Y") {
      await sync(codexHome);
      return;
    }
    console.log("已取消，未修改任何内容。");
  } finally {
    prompter.close();
  }
}

async function listRolloutFiles(rootDir) {
  let entries;
  try {
    entries = await fs.readdir(rootDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(rootDir, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listRolloutFiles(fullPath));
    } else if (entry.isFile() && entry.name.startsWith("rollout-") && entry.name.endsWith(".jsonl")) {
      files.push(fullPath);
    }
  }
  return files;
}

async function readFirstLine(filePath) {
  const handle = await fs.open(filePath, "r");
  try {
    let offset = 0;
    let collected = Buffer.alloc(0);
    while (true) {
      const chunk = Buffer.alloc(64 * 1024);
      const { bytesRead } = await handle.read(chunk, 0, chunk.length, offset);
      if (bytesRead === 0) {
        break;
      }
      offset += bytesRead;
      collected = Buffer.concat([collected, chunk.subarray(0, bytesRead)]);
      const newlineIndex = collected.indexOf(0x0a);
      if (newlineIndex !== -1) {
        const lineBuffer = newlineIndex > 0 && collected[newlineIndex - 1] === 0x0d
          ? collected.subarray(0, newlineIndex - 1)
          : collected.subarray(0, newlineIndex);
        return lineBuffer.toString("utf8");
      }
    }
    return collected.toString("utf8");
  } finally {
    await handle.close();
  }
}

function recordHasUserEvent(record) {
  if (record?.type === "event_msg" && record.payload?.type === "user_message") {
    return true;
  }
  for (const key of ["payload", "item", "msg"]) {
    const value = record?.[key];
    if (value?.type === "message" && value.role === "user") {
      return true;
    }
  }
  return false;
}

async function fileHasUserEvent(filePath) {
  let text;
  try {
    text = await fs.readFile(filePath, "utf8");
  } catch {
    return false;
  }
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) {
      continue;
    }
    try {
      if (recordHasUserEvent(JSON.parse(line))) {
        return true;
      }
    } catch {
      // Ignore malformed non-metadata lines.
    }
  }
  return false;
}

async function readRolloutSessions(codexHome) {
  const rolloutFiles = await listRolloutFiles(path.join(codexHome, "sessions"));
  const sessions = [];
  for (const filePath of rolloutFiles) {
    try {
      const firstLine = await readFirstLine(filePath);
      const record = JSON.parse(firstLine);
      if (record?.type !== "session_meta" || !record.payload) {
        continue;
      }
      const payload = record.payload;
      sessions.push({
        id: payload.id || payload.session_id || path.basename(filePath, ".jsonl"),
        provider: payload.model_provider || "(missing)",
        cwd: payload.cwd || "",
        timestamp: payload.timestamp || record.timestamp || "",
        source: payload.source || "vscode",
        threadSource: payload.thread_source || "user",
        cliVersion: payload.cli_version || "",
        hasUserEvent: await fileHasUserEvent(filePath),
        filePath
      });
    } catch {
      // Skip malformed or locked rollout files in the local display layer.
    }
  }
  return sessions;
}

async function readSessionIndexNames(codexHome) {
  const indexPath = path.join(codexHome, "session_index.jsonl");
  const names = new Map();
  let text;
  try {
    text = await fs.readFile(indexPath, "utf8");
  } catch {
    return names;
  }

  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) {
      continue;
    }
    try {
      const item = JSON.parse(line);
      if (!item.id) {
        continue;
      }
      const previous = names.get(item.id);
      const previousTime = Date.parse(previous?.updated_at || "");
      const nextTime = Date.parse(item.updated_at || "");
      if (!previous || !Number.isFinite(previousTime) || nextTime >= previousTime) {
        names.set(item.id, {
          name: item.thread_name,
          updated_at: item.updated_at
        });
      }
    } catch {
      // Ignore malformed index lines.
    }
  }
  return names;
}

async function readSqliteThreadRows(codexHome) {
  const dbPath = await existingStateDbPath(codexHome);
  const rowsById = new Map();
  if (!dbPath) {
    return rowsById;
  }

  let db;
  try {
    db = await openDatabase(dbPath, { readOnly: true });
    const rows = db.prepare(`
      SELECT id, title, model_provider, archived, cwd
      FROM threads
    `).all();
    for (const row of rows) {
      rowsById.set(row.id, row);
    }
  } catch {
    // Status already reports SQLite problems; keep the detail list usable.
  } finally {
    db?.close();
  }
  return rowsById;
}

async function buildConversationDetails(codexHome) {
  const [rollouts, indexNames, sqliteRows] = await Promise.all([
    readRolloutSessions(codexHome),
    readSessionIndexNames(codexHome),
    readSqliteThreadRows(codexHome)
  ]);

  return rollouts
    .map((session) => {
      const indexName = indexNames.get(session.id)?.name;
      const sqliteRow = sqliteRows.get(session.id);
      return {
        ...session,
        name: cleanText(indexName || sqliteRow?.title || session.id),
        sqliteTitle: sqliteRow?.title ? cleanText(sqliteRow.title) : "",
        inSqlite: Boolean(sqliteRow),
        archived: sqliteRow?.archived ? "是" : "否",
        indexUpdatedAt: indexNames.get(session.id)?.updated_at || ""
      };
    })
    .sort((left, right) => {
      const rightTime = Date.parse(right.indexUpdatedAt || right.timestamp || "");
      const leftTime = Date.parse(left.indexUpdatedAt || left.timestamp || "");
      return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0);
    });
}

function getSqliteSessionCounts(status) {
  if (status.sqliteCounts && !status.sqliteCounts.unreadable) {
    return status.sqliteCounts.sessions || {};
  }
  return {};
}

function getRepairTotal(status, hiddenCount) {
  const userEvent = Number(status.sqliteRepairStats?.userEventRowsNeedingRepair || 0);
  const cwd = Number(status.sqliteRepairStats?.cwdRowsNeedingRepair || 0);
  return {
    userEvent,
    cwd,
    total: hiddenCount + userEvent + cwd
  };
}

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function timestampForPath() {
  const date = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

async function backupSqliteFiles(codexHome, dbPath) {
  const backupDir = path.join(codexHome, "backups_state", "provider-sync-local-backfill", timestampForPath());
  await fs.mkdir(backupDir, { recursive: true });
  for (const sourcePath of [dbPath, `${dbPath}-wal`, `${dbPath}-shm`]) {
    if (await fileExists(sourcePath)) {
      await fs.copyFile(sourcePath, path.join(backupDir, path.basename(sourcePath)));
    }
  }
  return backupDir;
}

function buildInsertRow(conversation) {
  const title = cleanText(conversation.name || conversation.id);
  const createdAt = toUnixSeconds(conversation.timestamp);
  const createdAtMs = toUnixMs(conversation.timestamp);
  const updatedAtMs = Number.isFinite(Date.parse(conversation.indexUpdatedAt || ""))
    ? Date.parse(conversation.indexUpdatedAt)
    : createdAtMs;
  const updatedAt = Math.floor(updatedAtMs / 1000);

  return {
    id: conversation.id,
    rollout_path: conversation.filePath,
    created_at: createdAt,
    updated_at: updatedAt,
    source: conversation.source || "vscode",
    model_provider: conversation.provider || "custom",
    cwd: conversation.cwd || "",
    title,
    sandbox_policy: '{"type":"disabled"}',
    approval_mode: "never",
    tokens_used: 0,
    has_user_event: conversation.hasUserEvent ? 1 : 0,
    archived: 0,
    archived_at: null,
    git_sha: null,
    git_branch: null,
    git_origin_url: null,
    cli_version: conversation.cliVersion || "",
    first_user_message: title,
    agent_nickname: null,
    agent_role: null,
    memory_mode: "enabled",
    model: null,
    reasoning_effort: null,
    agent_path: null,
    created_at_ms: createdAtMs,
    updated_at_ms: updatedAtMs,
    thread_source: conversation.threadSource || "user",
    preview: title
  };
}

async function backfillMissingThreads(codexHome) {
  const dbPath = await existingStateDbPath(codexHome);
  if (!dbPath) {
    throw new Error("找不到 state_5.sqlite，无法入库。");
  }

  const conversations = await buildConversationDetails(codexHome);
  const missing = conversations.filter((conversation) => !conversation.inSqlite);
  const backupDir = await backupSqliteFiles(codexHome, dbPath);
  if (missing.length === 0) {
    return { inserted: 0, backupDir, ids: [] };
  }

  let db;
  try {
    db = await openDatabase(dbPath);
    db.exec("PRAGMA busy_timeout = 5000");
    db.exec("BEGIN IMMEDIATE");
    const insert = db.prepare(`
      INSERT INTO threads (
        id, rollout_path, created_at, updated_at, source, model_provider, cwd,
        title, sandbox_policy, approval_mode, tokens_used, has_user_event,
        archived, archived_at, git_sha, git_branch, git_origin_url,
        cli_version, first_user_message, agent_nickname, agent_role,
        memory_mode, model, reasoning_effort, agent_path, created_at_ms,
        updated_at_ms, thread_source, preview
      ) VALUES (
        @id, @rollout_path, @created_at, @updated_at, @source, @model_provider, @cwd,
        @title, @sandbox_policy, @approval_mode, @tokens_used, @has_user_event,
        @archived, @archived_at, @git_sha, @git_branch, @git_origin_url,
        @cli_version, @first_user_message, @agent_nickname, @agent_role,
        @memory_mode, @model, @reasoning_effort, @agent_path, @created_at_ms,
        @updated_at_ms, @thread_source, @preview
      )
    `);
    for (const conversation of missing) {
      insert.run(buildInsertRow(conversation));
    }
    db.exec("COMMIT");
  } catch (error) {
    try {
      db?.exec("ROLLBACK");
    } catch {
      // Keep the original error.
    }
    throw error;
  } finally {
    db?.close();
  }

  return {
    inserted: missing.length,
    backupDir,
    ids: missing.map((conversation) => conversation.id)
  };
}

async function printStatus(codexHome) {
  const [status, conversations] = await Promise.all([
    getStatus({ codexHome }),
    buildConversationDetails(codexHome)
  ]);
  const rolloutSessionCounts = status.rolloutCounts.sessions || {};
  const sqliteSessionCounts = getSqliteSessionCounts(status);
  const rolloutTotal = sumCounts(rolloutSessionCounts);
  const sqliteTotal = sumCounts(sqliteSessionCounts);
  const notInSqlite = Math.max(0, rolloutTotal - sqliteTotal);
  const hiddenThreads = countHidden(rolloutSessionCounts, status.currentProvider);
  const repair = getRepairTotal(status, hiddenThreads);
  const sqliteDatabase = status.stateDbLocation?.path || "未找到";
  const sqliteCountsText = status.sqliteCounts?.unreadable
    ? `不可读取：${status.sqliteCounts.error || "数据库异常"}`
    : `数据库 ${sqliteDatabase}；入库对话 ${sqliteTotal} 条（${formatCounts(sqliteSessionCounts)}）；未入库对话文件 ${notInSqlite} 条；user-event 待修 ${repair.userEvent} 条；cwd 路径待修 ${repair.cwd} 条`;

  console.log(`对话仓库：${status.codexHome}`);
  console.log("");
  console.log(`本电脑有 ${status.configuredProviders.length} 个 provider：${status.configuredProviders.join("，")}；当前的 provider 是：${status.currentProvider}`);
  console.log("");
  console.log(`对话记录有 ${rolloutTotal} 条；分别是：${formatCounts(rolloutSessionCounts)}；隐藏的对话有：${hiddenThreads} 条`);
  console.log("");
  console.log("对话明细：");
  conversations.forEach((conversation, index) => {
    const sqliteStatus = conversation.inSqlite ? "已入库" : "未入库";
    const idLabel = padDisplay(`${index + 1}. ID：${conversation.id}`, 50);
    const nameLabel = padDisplay(`Desktop 命名：${shorten(conversation.name, 30)}`, 52);
    const cwdLabel = padDisplay(`cwd：${shorten(conversation.cwd || "无", 52)}`, 58);
    console.log(`  ${idLabel}${nameLabel}${cwdLabel}（${sqliteStatus}）`);
    console.log("");
  });
  console.log("");
  console.log(`SQLite state：${sqliteCountsText}`);
  console.log("");
  console.log("Project visibility：");
  console.log(`  ${formatProjectVisibility(status.projectThreadVisibility)}`);
  console.log("");
  console.log(`同步备份在：${status.backupRoot}`);
  console.log(`对话恢复备份在：${conversationBackupRoot(status.codexHome)}`);
  console.log("");
  console.log(`预计恢复/修复的条数：${repair.total} 条（隐藏 ${hiddenThreads}，user-event ${repair.userEvent}，cwd ${repair.cwd}）`);
  console.log("");
  console.log(`未入库对话：${notInSqlite} 条。输入 B 可将未入库对话写入 SQLite。`);
  console.log("");
}

async function sync(codexHome) {
  const result = await runSync({
    codexHome,
    onProgress(event) {
      if (event?.status !== "start") {
        return;
      }
      const labels = {
        scan_rollout_files: "扫描对话文件",
        check_locked_rollout_files: "检查文件锁定",
        create_backup: "创建备份",
        update_sqlite: "更新 SQLite",
        rewrite_rollout_files: "更新对话文件",
        clean_backups: "清理旧备份"
      };
      if (labels[event.stage]) {
        console.log(`正在${labels[event.stage]}...`);
      }
    }
  });

  console.log("保存完成。");
  console.log(`目标 provider：${result.targetProvider}`);
  console.log(`本次备份：${result.backupDir}`);
  console.log(`已更新对话文件：${result.changedSessionFiles} 个`);
  console.log(`已更新 SQLite provider 行：${result.sqliteProviderRowsUpdated ?? result.sqliteRowsUpdated} 行`);
  console.log(`已修复 user-event：${result.sqliteUserEventRowsUpdated || 0} 行`);
  console.log(`已修复 cwd 路径：${result.sqliteCwdRowsUpdated || 0} 行`);
  console.log(`已更新项目路径缓存：${result.updatedWorkspaceRoots || 0} 个`);
}

async function backfill(codexHome) {
  const result = await backfillMissingThreads(codexHome);
  console.log("入库完成。");
  console.log(`本次备份：${result.backupDir}`);
  console.log(`新增 SQLite threads：${result.inserted} 条`);
  if (result.ids.length > 0) {
    console.log(`新增 ID：${result.ids.join("，")}`);
  }
}

const command = process.argv[2] || "status";
const codexHome = process.argv[3] || process.env.CODEX_HOME;

try {
  if (command === "status") {
    await printStatus(codexHome);
  } else if (command === "sync") {
    await sync(codexHome);
  } else if (command === "backfill") {
    await backfill(codexHome);
  } else if (command === "interactive") {
    await interactive(codexHome);
  } else {
    throw new Error(`未知命令：${command}`);
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
