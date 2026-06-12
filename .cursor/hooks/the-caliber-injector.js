// sessionStart hook: inject the-caliber skill into agent context.
// Skill source: .cursor/skills/the-caliber/SKILL.md
// Purpose: the execution floor must be installed, not discovered — a cheaper
// model is exactly the one that won't choose to load a discipline skill.

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
  return path.join(projectDir, ".cursor", "skills", "the-caliber", "SKILL.md");
}

function buildContext(skillContent) {
  return (
    `<EXTREMELY-IMPORTANT>\n` +
    `the-caliber skill is ALWAYS-ON this session — it is the execution floor for every working turn, on any model.\n` +
    `\n` +
    `Governing law: never assert what you have not observed, never edit what you have not read, never declare done what you have not verified, never answer less than the whole message.\n` +
    `Per-turn floor: multi-part message → build the ask-ledger first. Any edit → read the file first, read signatures from source. Any "done" → re-read the user's literal message and walk the ledger with per-item status + verification. Any failure → change exactly one thing per retry; three failures → switch strategy. "Should work" and bare "done!" are banned — name what ran, or say "not verified".\n` +
    `Action-delta inheritance: trivial turns and pure chat pass through untaxed — no ledger, no ceremony.\n` +
    `\n` +
    `Full skill below. It sits BENEATH the rest of the family (the-inverse, the-prescription, birdseye, the-conviction) — they specialize on top of this floor, never replace it.\n` +
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
    process.stderr.write(`the-caliber injector skipped: ${err.message}\n`);
    process.stdout.write(JSON.stringify({}));
  }
}

main();
