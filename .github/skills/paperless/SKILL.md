---
name: paperless
description: "Paperless-ngx document management setup — architecture, LLM auto-classification via paperless-gpt, taxonomy, API access, and operational patterns. USE FOR: modifying Paperless config, adding document types/tags/correspondents, troubleshooting OCR or classification, updating paperless-gpt prompts or model config."
applyTo: "stacks/optiplex.yml,stacks/homepage/**,inventory/**"
---

# Paperless-ngx Setup

## Architecture

Three containers on **optiplex** in a shared `paperless-net` bridge network:

| Container | Image | Role |
|---|---|---|
| `paperless-redis` | `redis:7-alpine` | Cache/broker for Paperless |
| `paperless-ngx` | `ghcr.io/paperless-ngx/paperless-ngx:latest` | Document management, OCR |
| `paperless-gpt` | `ghcr.io/icereed/paperless-gpt:latest` | LLM auto-classification |

```
PDF upload → paperless-ngx (OCR: tesseract eng+ell)
          → tagged "sys:paperless-gpt-auto"
          → paperless-gpt picks it up
          → calls Ollama on localllm (qwen3:8b-fast)
          → sets title, tags, correspondent, doc type, created date
          → removes the auto tag
```

## Access

- **Web UI**: `https://paperless.internal` (reverse-proxied via Caddy)
- **paperless-gpt UI**: `https://paperless-gpt.internal`
- **API**: No published port — access via `docker exec`:
  ```
  ssh -F ssh.config optiplex \
    'docker exec paperless-ngx curl -s \
      -H "Authorization: Token <token>" \
      "http://localhost:8000/api/<endpoint>/?format=json"'
  ```
- **API Token**: stored in `vault_paperless_api_token` (Ansible Vault)

## Document intake

- **Auto-consume folder**: `/mnt/media/paperless-consume` on optiplex, mounted at `/usr/src/paperless/consume`
- **Web upload**: via the Paperless UI
- After OCR, documents tagged `sys:paperless-gpt-auto` are picked up by paperless-gpt for classification

## LLM classification (paperless-gpt)

- **Model**: `qwen3:8b-fast` (no-thinking variant, ~5 min/doc on CPU)
- **Provider**: Ollama on `localllm.internal:11434`
- **Context**: 16384 tokens, token limit 5000 per document
- **Rate limit**: 4 requests/minute
- **Auto-generates**: title, tags, correspondent, document type, created date
- **CREATE_NEW_TAGS**: `false` — only assigns from existing tags
- **Prompt templates**: stored in Docker volume `paperless-gpt-prompts` (NOT in IaC)
- Key env vars: `AUTO_TAG: "sys:paperless-gpt-auto"`, `LLM_LANGUAGE: "English or Greek"`

## OCR

- **Engine**: tesseract via ocrmypdf
- **Languages**: `eng+ell` (English + Greek)
- Ghostscript CMap warnings on non-conformant PDFs are harmless (auto-repaired)

## Taxonomy (IaC-managed, append-only)

Defined in `inventory/group_vars/all/main.yml` — four lists:
- `paperless_document_types` — list of strings
- `paperless_tags` — list of strings (prefixed by category: `med:`, `fin:`, `geo:`, etc.)
- `paperless_correspondents` — list of strings
- `paperless_custom_fields` — list of `{name, data_type}` objects

Tasks in `deploy-stacks.yml` POST each item to the Paperless API on every deploy. Duplicates return 400 and are silently ignored (append-only).

**To add taxonomy items**: append to the list in `main.yml`, run `make stacks`.
**To rename/delete**: do it in the Paperless UI — IaC is create-only.

## Config locations

| What | Where |
|---|---|
| Service definitions | `stacks/optiplex.yml` (lines ~520–620) |
| Taxonomy | `inventory/group_vars/all/main.yml` — `paperless_*` vars |
| Volumes | `paperless-data`, `paperless-media`, `paperless-redis-data`, `paperless-gpt-prompts` |
| DNS aliases | `inventory/group_vars/all/main.yml` — optiplex aliases include `paperless`, `paperless-gpt` |
| Caddy proxy | `proxy_services` in `inventory/group_vars/all/main.yml` |
| Secrets | `inventory/group_vars/all/vault.yml` — `vault_paperless_secret_key`, `vault_paperless_admin_user`, `vault_paperless_admin_password`, `vault_paperless_api_token` |
| Homepage widget | `stacks/homepage/services.yaml.j2` |

## Common operations

**Add a new tag**: Append to `paperless_tags` in `main.yml`, run `make stacks`.
**Add a document type**: Append to `paperless_document_types` in `main.yml`, run `make stacks`.
**Add a correspondent**: Append to `paperless_correspondents` in `main.yml`, run `make stacks`.
**Bulk re-process with LLM**: Tag documents with `sys:paperless-gpt-auto` via bulk edit in the UI — paperless-gpt will re-classify them.
**Check processing status**: Look at paperless-gpt container logs in Dozzle.
**Change LLM model**: Update `LLM_MODEL` in `stacks/optiplex.yml`, then `make stacks`.
