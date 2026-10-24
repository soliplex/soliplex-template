## `haiku-rag-slim` version compatibility

`soliplex`, the configurations in this stack, and the RAG databases served
by its rooms, all depend on a specific, pinned range of `haiku-rag-slim`
versions. Keeping the version of the image specified in `docker-compose.yml`
in sync with that range should follow a documented process (see
`../docs/getting-started/upgrading-stack.md`).

## Using the stock ingester image

As generated, the `haiku-ingester` service and the one-shot `haiku-rag` CLI
runner use a shared anchor to the upstream image as published, without
a local build. E.g.:

```yaml
  haiku-ingester:
    image: &haiku_rag_image ghcr.io/ggozad/haiku.rag-slim:P.Q.R

    #build:
    #  context: haiku.rag
    #  dockerfile: Dockerfile

...

  haiku-rag:
    image: *haiku_rag_image

    #build:
    #  context: haiku.rag
    #  dockerfile: Dockerfile
```

This shared, pulled image version is the one managed by the upgrade process.

## Building the ingester image locally

To use a non-default build of the `haiku-rag*` image for your deployment,
create a `Dockerfile` in this directory, e.g.:

```Dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ghcr.io/ggozad/haiku.rag-slim:P.Q.R
FROM ${BASE_IMAGE}
```

and add the needful additional steps.

Then comment out or remove the `image:` stanza for the `haiku-rag`
service, replacing it with a `build:` stanza which fits your needs.  E.g.:

```yaml
  haiku-ingester:
    # image: ...

    build:
      context: haiku.rag
      dockerfile: Dockerfile
    # Override ARG here if needed
    # args:
    #   BASE_IMAGE: "quay.io/ggozad/haiku.rag-slim:U.V.W"

...

  haiku-rag:
    # image: *haiku_rag_image

    build:
      context: haiku.rag
      dockerfile: Dockerfile
    # Override ARG here if needed
    # args:
    #   BASE_IMAGE: "quay.io/ggozad/haiku.rag-slim:U.V.W"
```

At this point, you are off the beaten migration path specified in the
document above, and should proceed with care.
