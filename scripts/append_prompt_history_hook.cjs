const fs = require("fs");
const path = require("path");

const HIST_FILENAME = "prompt_history.md";
const PROJECT_NAME = "RagProject";
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

const projectRoot = path.resolve(__dirname, "..");
const fallbackHistPath = path.join(projectRoot, HIST_FILENAME);

const rowRe = /^\|\s*(\d+)\s*\|\s*(\d{8})\s*\|\s*(\d{2}:\d{2})\s*\|/;
const separatorRe = /^\|\s*-+/;
const countRe = /^\*총\s+\d+개\s+항목\*/u;

function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

function findHistPath(cwd) {
  const candidates = [];

  if (typeof cwd === "string" && cwd.trim()) {
    candidates.push(path.join(cwd, HIST_FILENAME));
  }
  candidates.push(fallbackHistPath);

  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function nowKstParts(now) {
  const kst = new Date(now.getTime() + KST_OFFSET_MS);
  return {
    date: `${kst.getUTCFullYear()}${pad2(kst.getUTCMonth() + 1)}${pad2(kst.getUTCDate())}`,
    time: `${pad2(kst.getUTCHours())}:${pad2(kst.getUTCMinutes())}`,
  };
}

function parseKstDateTime(dateText, timeText) {
  const year = Number(dateText.slice(0, 4));
  const month = Number(dateText.slice(4, 6));
  const day = Number(dateText.slice(6, 8));
  const [hour, minute] = timeText.split(":").map(Number);
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute));
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}시간 ${minutes}분`;
  }
  return `${minutes}분 ${pad2(seconds)}초`;
}

function sanitizePrompt(prompt) {
  return prompt
    .trim()
    .replace(/\|/g, "｜")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/\n/g, " ↵ ");
}

function findLastRow(lines) {
  let last = null;

  lines.forEach((line, index) => {
    const match = line.match(rowRe);
    if (!match) {
      return;
    }

    last = {
      index,
      no: Number(match[1]),
      startedAt: parseKstDateTime(match[2], match[3]),
    };
  });

  return last;
}

function updateDurationColumn(line, duration) {
  const parts = line.split("|");
  if (parts.length < 7) {
    return line;
  }

  parts[4] = ` ${duration} `;
  return parts.join("|");
}

function findInsertIndex(lines, lastRowIndex) {
  if (lastRowIndex !== null && lastRowIndex !== undefined) {
    return lastRowIndex + 1;
  }

  const separatorIndex = lines.findIndex((line) => separatorRe.test(line));
  return separatorIndex === -1 ? lines.length : separatorIndex + 1;
}

function updateCount(lines, count) {
  const countLine = `*총 ${count}개 항목*`;
  const index = lines.findIndex((line) => countRe.test(line));

  if (index !== -1) {
    lines[index] = countLine;
    return;
  }

  if (lines.length > 0 && lines[lines.length - 1].trim()) {
    lines.push("");
  }
  lines.push(countLine);
}

function run() {
  const input = readStdin();
  if (!input.trim()) {
    return;
  }

  let data;
  try {
    data = JSON.parse(input);
  } catch {
    return;
  }

  const prompt = sanitizePrompt(String(data.prompt || ""));
  if (!prompt) {
    return;
  }

  const histPath = findHistPath(data.cwd || process.cwd());
  if (!histPath) {
    return;
  }

  const now = new Date();
  const { date, time } = nowKstParts(now);
  const lines = fs.readFileSync(histPath, "utf8").split(/\r?\n/);
  if (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }

  const lastRow = findLastRow(lines);
  const no = lastRow ? lastRow.no + 1 : 1;
  const insertIndex = findInsertIndex(lines, lastRow ? lastRow.index : null);

  if (lastRow) {
    lines[lastRow.index] = updateDurationColumn(
      lines[lastRow.index],
      formatDuration(now.getTime() - lastRow.startedAt.getTime())
    );
  }

  lines.splice(
    insertIndex,
    0,
    `| ${no} | ${date} | ${time} | — | ${PROJECT_NAME} | ${prompt} |`
  );
  updateCount(lines, no);

  fs.writeFileSync(histPath, `${lines.join("\n")}\n`, "utf8");
}

try {
  run();
} catch (err) {
  // 실패를 Claude Code에 노출하지 않고(exitCode=0) 로그에만 기록
  try {
    const logPath = path.join(path.resolve(__dirname, ".."), "logs", "prompt_history_hook_errors.log");
    const ts = new Date().toISOString();
    fs.appendFileSync(logPath, `[${ts}] ${err && err.stack ? err.stack : String(err)}\n`);
  } catch {
    // 로그 기록도 실패하면 조용히 종료
  }
  process.exitCode = 0;
}
