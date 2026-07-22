# Deploying the x-dog site

Two artifacts, mirroring how the sibling `depins` site is deployed:

- **`xdog-site.service`** — a systemd *user* service that runs the Flask app
  (`xdog-site`, i.e. `xdog_site.app:main`) on `127.0.0.1:8080` with
  `Restart=always`.
- **`xdog.conf`** — an nginx site that redirects HTTP to HTTPS and reverse-proxies
  `/` to the service via an `upstream` on `127.0.0.1:8080`.

## systemd (user service)

```bash
# from the repo root, ensure the console script exists
uv sync

cp packages/site/deploy/xdog-site.service ~/.config/systemd/user/xdog-site.service
# adjust WorkingDirectory / ExecStart paths if the repo lives elsewhere

systemctl --user daemon-reload
systemctl --user enable --now xdog-site.service
systemctl --user status xdog-site.service
curl -sS http://127.0.0.1:8080/ | head
```

The unit is `PartOf=zdog.target` (like the depins units) so it starts/stops with
that target; change `WantedBy`/`PartOf` to `default.target` if you don't use it.

## nginx

```bash
sudo cp packages/site/deploy/xdog.conf /etc/nginx/conf.d/xdog.conf
# edit server_name + ssl_certificate paths for your host
sudo nginx -t && sudo systemctl reload nginx
```

## Configuration (env vars read by `xdog-site`)

| Variable          | Default            | Purpose                       |
|-------------------|--------------------|-------------------------------|
| `XDOG_SITE_HOST`  | `127.0.0.1`        | bind host                     |
| `XDOG_SITE_PORT`  | `8080`             | bind port (matches upstream)  |
| `XDOG_SITE_SECRET`| dev placeholder    | Flask secret key              |
| `XDOG_SITE_DEBUG` | unset              | any value enables debug mode  |

> The bundled Flask server is fine for this low-traffic static-content site; put
> a real WSGI server (gunicorn/waitress) in front if you need more throughput.
