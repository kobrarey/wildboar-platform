# WildBoar deploy notes

## Deploy path standard

- code: `/opt/wildboar/current`
- env: `/opt/wildboar/shared/.env`
- venv: `/opt/wildboar/.venv`

## Env
Server env file:
`/opt/wildboar/shared/.env`

## Systemd units
Copy unit files from:
`deploy/systemd/`
to:
`/etc/systemd/system/`

Then run:
```bash
sudo systemctl daemon-reload
sudo systemctl enable wildboar-web
sudo systemctl restart wildboar-web
```

For workers:
```bash
sudo systemctl enable wildboar-worker-deposit-listener
sudo systemctl enable wildboar-worker-confirmations
sudo systemctl enable wildboar-worker-compliance
sudo systemctl enable wildboar-worker-balance-updater
sudo systemctl enable wildboar-worker-withdrawal
sudo systemctl enable wildboar-worker-telegram-watchdog
```

## Nginx
Copy:
`deploy/nginx/wildboar-preview.conf`
to:
`/etc/nginx/sites-available/wildboar-preview.conf`

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/wildboar-preview.conf /etc/nginx/sites-enabled/wildboar-preview.conf
sudo nginx -t
sudo systemctl reload nginx
```

## Web app
Production start is handled by systemd.

- code dir: `/opt/wildboar/current`
- env file: `/opt/wildboar/shared/.env`
- venv: `/opt/wildboar/.venv`