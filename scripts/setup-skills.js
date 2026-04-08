const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

// Check if OpenClaw is installed
function checkOpenClaw() {
  try {
    const version = execSync('openclaw --version', { stdio: 'pipe' }).toString().trim();
    console.log(`✓ OpenClaw installed: ${version}`);
    return true;
  } catch {
    try {
      // Some versions use -v
      execSync('openclaw -v', { stdio: 'pipe' });
      console.log('✓ OpenClaw installed');
      return true;
    } catch {
      console.warn('⚠️  OpenClaw not found in PATH.');
      console.warn('   Install it with: npm install -g openclaw');
      console.warn('   Then run: openclaw init');
      console.warn('   Continuing — skill files will be written but OpenClaw cannot load them until installed.\n');
      return false;
    }
  }
}

// Check if openclaw.json exists and warn if COSTCO_SCANNER_URL is not set
function checkConfig() {
  const configPath = path.join(os.homedir(), '.openclaw', 'openclaw.json');
  if (!fs.existsSync(configPath)) {
    console.warn('⚠️  ~/.openclaw/openclaw.json not found — run `openclaw init` first.');
    return;
  }
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const url = config?.skills?.entries?.['costco-scanner']?.COSTCO_SCANNER_URL;
    if (!url) {
      console.warn('⚠️  COSTCO_SCANNER_URL not set in openclaw.json.');
      console.warn('   Add it under skills.entries.costco-scanner:');
      console.warn('   { "COSTCO_SCANNER_URL": "https://<api-id>.execute-api.<region>.amazonaws.com" }');
    } else {
      console.log(`✓ COSTCO_SCANNER_URL: ${url}`);
    }
  } catch {
    console.warn('⚠️  Could not parse ~/.openclaw/openclaw.json');
  }
}

checkOpenClaw();

const repoSkillDir = path.join(__dirname, '..', 'skills', 'costco-scanner');
const liveSkillDir = path.join(os.homedir(), '.openclaw', 'skills', 'costco-scanner');

// Ensure both directories exist
fs.mkdirSync(repoSkillDir, { recursive: true });
fs.mkdirSync(liveSkillDir, { recursive: true });

// Read canonical SKILL.md from the repo
const skillMd = fs.readFileSync(path.join(repoSkillDir, 'SKILL.md'), 'utf8');

// Write to live OpenClaw skills directory
const livePath = path.join(liveSkillDir, 'SKILL.md');
fs.writeFileSync(livePath, skillMd, 'utf8');
console.log('✓ SKILL.md synced to:', livePath);

checkConfig();

console.log('\nSend /new in Telegram to start a fresh session and pick up the skill.');
