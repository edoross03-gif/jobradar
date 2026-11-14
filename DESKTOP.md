# Job Radar: editing from your PC

This folder is a copy of the live Job Radar code (`/opt/rossettilab/jobradar` on the server).
Edit it here, then upload. Nothing goes live until you run `./upload.sh apply`.

| What you edit | Effect after deploy |
|---|---|
| `config/companies.yaml`, `filters.yaml`, `profile.yaml` | picked up on the next run (every 2h), no restart |
| anything else (`jobradar/*.py`, Dockerfile, requirements) | container rebuilt + restarted automatically |

## Workflow
```
./upload.sh          # send folder, show what differs from live (changes nothing live)
./upload.sh apply    # send + run tests + confirm + backup + deploy
./download.sh        # pull live files back here (when the server changed something)
```
On the server: `/opt/jobradar/deploy.sh status | apply | rollback`. Backups go in `/opt/jobradar-backups/`.

Deploy **refuses** if a live file was changed on the server since your last deploy
(e.g. `python -m jobradar discover --write` rewrites `companies.yaml`). Run `./download.sh` first.

## Not uploaded
| Folder | What is in it |
|---|---|
| `docs/` | the project plan (`job-radar-plan.md` + PDF), the prompts already run on the server (`DEPLOY_PROMPT.md`, `ADD_COMPANIES_BATCH2.md`), the Grafana dashboard export (`grafana/`). On this PC only: not in git |
| `exports/` | CSV dumps written by `./export.sh` |
| `_archive/` | superseded copies (old `companies.yaml` / `filters.yaml` of 28/09, the Sprint 1 zip). Not in git |

`upload.sh` skips `docs/`, `exports/` and every `_*/` folder. The three `.md` files moved into `docs/` on 14/11/2026 still sit
at the top of the server copy: the next `./upload.sh apply` lists them as deleted and rebuilds the container once.

## Uploaded, but not in git
`config/profile.yaml` and `config/companies.yaml` (the real profile and company list) and `.deploy.env` (the server
address the scripts use) are git-ignored. The repository carries `*.example.*` versions instead. `upload.sh` still sends
the real config files to the server; `.deploy.env` stays on this PC.

## Never here
`.env` (DB URL, Telegram token): it stays only on the server and deploy never touches it.
No `.bak` copies: use git on your PC (`git init` once). Server-side `.bak` files and discover reports stay on the server.
Mute/unmute state lives in the database, not in these files: use `docker exec jobradar python -m jobradar mute ...`.

Windows: run the scripts from Git Bash or WSL (they need `rsync` and `ssh`).
