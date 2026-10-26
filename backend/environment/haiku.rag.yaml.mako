# haiku.rag configuration file
# See https://ggozad.github.io/haiku.rag/configuration/ for details

environment: development

storage:
  data_dir: ""
  vacuum_retention_seconds: 86400

# This section is for configuring LanceDB in various "remote" modes.
# Leave it commented out to use local filesystem databases.
#lancedb:
  # Configure LanceDB Cloud
  # api_key: ''
  # region: ''
  # databases:
  #   <name>: '<location>'

  # Configure Amazon S3
  # Use AWS credentials or IAM roles
  # databases:
  #   <name>: 's3://my-bucket/my-table'

  # Configure Azure Blob Storage
  # Use Azure credentials
  # databases:
  #   <name>: 'az://my-bucket/my-table'

  # Google Cloud Storage
  # Use GCP credentials
  # databases:
  #   <name>: 'gs://my-bucket/my-table'

  # Configure Hadoop file system
  # databases:
  #   <name>: 'hdfs://namenode:port/path/to/table'

# Match ingester's 'haiku.rag/haiku.rag.yaml', which writes the same LanceDB.
embeddings:
  model:
    name: ${rag_embed_model}
    provider: ollama
    vector_dim: ${rag_embed_dim}

# Omit the 'reranking' section to disable re-ranking.
#reranking:
# model:
#   name: ''
#   provider: ''

# 'max_searches' bounds a multi-step question (default 5); it replaced the
# iteration bound of the removed 'research' section.
qa:
  model:
    name: ${rag_qa_model}
    provider: ollama
  #max_searches: 5

processing:
  chunk_size: ${chunk_size}
  converter: docling-serve  # Use remote conversion
  chunker: docling-serve    # Use remote chunking

providers:
  # The docling-serve url if you have not installed the full haiku.rag package.
  docling_serve:
    base_url: http://localhost:5001

  #
  #  Leave the 'ollama' section commented out to allow it's base URL to be
  #  populated from 'os.environ' when using the 'haiku-rag' CLI.
  #
  #  In the soliplex server, this value will be replaced by the value
  #  configured for 'OLLAMA_BASE_URL' in the installation environment (if set).
  #
  #  ollama:
  #   base_url: http://localhost:11434

  #  vLLM (or any OpenAI-compatible endpoint) is reached per-model now:
  #  set 'base_url' inside an individual 'model' block above, rather than
  #  once for the whole provider.
