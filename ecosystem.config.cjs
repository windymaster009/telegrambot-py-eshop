module.exports = {
  apps: [
    {
      name: "telegram-shop-bot",
      cwd: __dirname,
      script: "main.py",
      interpreter: `${__dirname}/.venv/bin/python`,
      autorestart: true,
      max_memory_restart: "350M",
      time: true,
    },
    {
      name: "telegram-shop-api",
      cwd: __dirname,
      script: `${__dirname}/.venv/bin/uvicorn`,
      args: "app.api:app --host 127.0.0.1 --port 8000 --proxy-headers",
      interpreter: "none",
      autorestart: true,
      max_memory_restart: "350M",
      time: true,
    },
    {
      name: "telegram-payment-listener",
      cwd: __dirname,
      script: "payment_listener.py",
      interpreter: `${__dirname}/.venv/bin/python`,
      autorestart: true,
      stop_exit_codes: [0],
      max_memory_restart: "250M",
      time: true,
    },
  ],
};
