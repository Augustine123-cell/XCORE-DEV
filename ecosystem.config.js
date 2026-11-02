// PM2: pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [{
    name: "xcore",
    script: "python3",
    args: "-m xcore",
    interpreter: "none",
    cwd: __dirname,
    instances: 1,
    autorestart: true,
    max_restarts: 50,
    watch: false,
    max_memory_restart: "500M",
    env: { PYTHONUNBUFFERED: "1" },
    error_file: "./logs/err.log",
    out_file: "./logs/out.log",
    time: true,
  }],
};
