# Hosted OpenAI-compatible inference instead of Ollama

Loads when: Load this reference when a stack's chat or embedding models come from a hosted OpenAI-compatible endpoint (DigitalOcean serverless inference, vLLM, OpenRouter, a gateway) instead of Ollama — when generating such a stack, or switching an existing one.

## What changes

The generator takes only an Ollama provider (`ollama_base_url`). For any
other OpenAI-compatible endpoint, generate as usual with that provider's
model ids, then make the post-generation edits in *Wiring the provider*.
Every edit is to a committed file except the key, which goes in the
gitignored `.secrets/`.

Throughout, `$BASE` is the endpoint's OpenAI-compatible base URL (ending in
`/v1`), and `$K` is its API key. Never accept the key inline. The user puts
it in a file, and the skill reads it from there only to copy it into
`.secrets/` and to probe:

```bash
K=$(cat /path/to/key-file)
```

## Choosing and probing models

List what the key can reach, and offer real choices from that list:

```bash
curl -s -H "Authorization: Bearer $K" "$BASE/models" | jq -r '.data[].id'
```

Keep the chat models and the embedding model in separate questions, as
GENERATION.md does for Ollama. Probe each chosen chat-style model
(`chat_model`, `chat_model_alt`, `title_model`, `rag_qa_model`) and the
embedding model (`rag_embed_model`) before generating:

```bash
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  "$BASE/chat/completions" \
  -d '{"model":"<id>","messages":[{"role":"user","content":"ping"}],"max_tokens":20}'

curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  "$BASE/embeddings" -d '{"model":"<id>","input":"ping"}' \
  | jq '.data[0].embedding | length'
```

- **Judge a chat probe by its HTTP status, not its text.** Reasoning models
  return `reasoning_content`, and with a small `max_tokens` can spend every
  token reasoning and return `content: null`. Soliplex streams the
  reasoning as AG-UI `REASONING_*` events, which the client shows.
- **Take `rag_embed_dim` from the embeddings probe**, never from memory: the
  length it prints is the dimension.
- **Match the chunking tokenizer to the embedder** where you can. The
  ingester's `haiku.rag/haiku.rag.yaml` sets `processing.chunking_tokenizer`
  (default `Qwen/Qwen3-Embedding-0.6B`), and chunk sizes are counted in that
  tokenizer's tokens.
- **Embedding is billed per token.** Tell the user before they ingest a large
  corpus.

## Generation inputs

Follow GENERATION.md, with these differences:

- `ollama_base_url`: the generator still requires it. Pass a placeholder
  (`http://127.0.0.1:11434`); after the wiring below nothing uses it.
- Skip the Ollama model probes, and after the stack is up, skip
  `soliplex-cli audit ollama` and `ollama pull`: there are no Ollama models
  left to check.
- The model parameters take the provider's ids, and `rag_embed_dim` comes from
  the probe.

## Wiring the provider

Run these from the stack directory after generating and before
`stack-init`. The names below use `inference_key` for the Docker secret and
`INFERENCE_KEY` for the Soliplex secret; pick names that say which
provider.

1. **The key.** Copy it into a Docker secret file, without echoing it:

   ```bash
   install -m 0600 /path/to/key-file .secrets/inference_key
   ```

   In `docker-compose.yml`, declare it beside the generated secrets:

   ```yaml
   secrets:
     # ...
     inference_key:
       file: ./.secrets/inference_key
   ```

   Then add `- inference_key` to the `secrets:` list of every service that
   resolves a model:

   - `backend`
   - `soliplex-dev` and `stack-init`, which share the
     `&soliplex_dev_secrets` anchor, so add it there once
   - `haiku-ingester`
   - `haiku-rag`

2. **Soliplex agents.** In `backend/environment/installation.yaml`, declare
   the secret:

   ```yaml
   secrets:
     - secret_name: "INFERENCE_KEY"
       sources:
         - kind: "file_path"
           file_path: /run/secrets/inference_key
     # ... the generated secrets
   ```

   Then give every entry in `agent_configs` the provider fields, next to its
   `model_name`. The generated entries are `default_chat`, `alternate_chat`,
   `provisioned_rag`, and `title`:

   ```yaml
       provider_type: "openai"
       provider_base_url: "<BASE>"
       provider_key: "secret:INFERENCE_KEY"
   ```

   Soliplex's `file_path` secret source strips surrounding whitespace, so a
   trailing newline in the key file is harmless.

3. **haiku.rag.** Both `backend/environment/haiku.rag.yaml` (read by the
   backend) and `haiku.rag/haiku.rag.yaml` (the ingester's, which writes the
   LanceDB) set `provider: ollama` on each model block. Switch the
   `embeddings.model` block in both files, and the backend file's
   `qa.model` block, to:

   ```yaml
       provider: openai
       base_url: <BASE>
       api_key: ${file:/run/secrets/inference_key}
   ```

   haiku.rag (0.104 and later, at least) expands `${file:PATH}` to the
   file's contents, minus a trailing newline. An absolute path ignores the
   config directory, so the ingester's shell entrypoint needs no `export`
   for this key.

4. **Verify.** Run `docker compose config --quiet`, then build, run
   `stack-init`, and bring the stack up as GENERATION.md describes. Once the
   backend is healthy, the resolved providers should name only the hosted
   endpoint:

   ```bash
   curl -s http://localhost:<nginx_http>/api/v1/installation/providers
   # {"openai":{"<BASE>":{"<chat id>":"chat","<embedding id>":"embedding"}}}
   ```

   Then drop a small markdown file into `rag/docs/`. Within seconds it should
   show as a `succeeded` job in `docker compose logs haiku-ingester`, which
   proves docling-serve, the embedder, and the key all work. Remove the file
   afterwards; `delete_orphans` drops its document.

Rooms the provisioner creates use the `provisioned_rag` agent and the
stack-wide `haiku.rag.yaml`, so they pick up these edits too.
