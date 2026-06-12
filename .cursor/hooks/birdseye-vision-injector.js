// sessionStart hook: inject birdseye-vision skill into agent context.
// Skill source: .cursor/skills/birdseye-vision/SKILL.md

const fs = require("fs");
const path = require("path");

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
  });
}

function resolveSkillPath() {
  const projectDir =
    process.env.CURSOR_PROJECT_DIR ||
    process.env.CLAUDE_PROJECT_DIR ||
    process.cwd();
  return path.join(
    projectDir,
    ".cursor",
    "skills",
    "birdseye-vision",
    "SKILL.md"
  );
}

function buildContext(skillContent) {
  return (
    `<EXTREMELY-IMPORTANT>\n` +
    `birdseye-vision skill is AUTO-ACTIVE this session — applies in every project, every turn.\n` +
    `\n` +
    `Operating principle: VISION → PROCESS → ACTION (never action-first on strategic tasks).\n` +
    `Hybrid trigger: fire on goal/multi-path/architectural/vision-language; skip on trivial execution.\n` +
    `Sensitivity: re-scan every 5 turns on sustained threads, on new nouns, on implementation verbs, on stacked-AND, on mood shift to vision-language. Inheritance resets every 3 continuation turns.\n` +
    `Branch to work-file skill when picked path is real cross-file shipping (>4 files, >1 session, "let's build/ship/implement", new convention/folder/template/hook/skill, cross-package, unresolved architecture decision).\n` +
    `Soft-surface: show pre-action block, proceed unless irreversible.\n` +
    `Auto-save: bloodline themes + surprising process insights + Stances to memory.\n` +
    `\n` +
    `Per-turn reminder: classify Step 0 BEFORE any Skill tool. Type B → one line. Type C/D → visible block before first non-trivial tool call. ACTION-DELTA: no rejected path → no block.\n` +
    `\n` +
    `Full skill below. Apply it whenever the trigger check fires this turn.\n` +
    `\n---\n\n` +
    skillContent +
    `\n</EXTREMELY-IMPORTANT>\n`
  );
}

async function main() {
  await readStdin();

  try {
    const skillPath = resolveSkillPath();
    const content = fs.readFileSync(skillPath, "utf8");
    const additional_context = buildContext(content);
    process.stdout.write(JSON.stringify({ additional_context }));
  } catch (err) {
    process.stderr.write(`birdseye-vision injector skipped: ${err.message}\n`);
    process.stdout.write(JSON.stringify({}));
  }
}

main();
