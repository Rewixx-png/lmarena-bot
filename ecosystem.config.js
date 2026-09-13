const path = require("path");

module.exports = {
  apps: [
    {
      name: "lmarena-bot",
      script: "bot.py",
      interpreter: path.join(__dirname, "venv", "bin", "python"),
      cwd: __dirname,
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 10000,
      max_memory_restart: "250M",
      out_file: path.join(__dirname, "logs", "out.log"),
      error_file: path.join(__dirname, "logs", "err.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
