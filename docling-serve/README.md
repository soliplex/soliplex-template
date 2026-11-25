## `docling-serve` version compatibility

The `docling-serve` image version is loosely coupled in this stack
to `haiku-rag`, and only transitively to `soliplex`.

As with any dependency, it has over time had behavioral quirks and
changes which affect the quality of the `haiku-ingester`'s output, or even
cause it to fail outright for some documents.  At such times,
the `haiku-rag-slim` version may pin the `docling-*` packages to
versions known to work.

The upgrade process for keeping `haiku-rag-slim` in step with `soliplex`
([Upgrading an existing stack](https://soliplex.github.io/soliplex-template/upgrades/))
doesn't really cover this problem directly.

## Using the stock `docling-serve` image

As generated, the `docling-serve` service uses an upstream image as published,
without a local build. E.g.:

```yaml
  docling-serve:
    image: quay.io/docling-project/docling-serve-cpu:vK.L.M

    #build:
    #  context: docling-serve
    #  dockerfile: Dockerfile
```

The image's suffix comes from the `docling_serve_variant` chosen at
generation:

| Variant | Image |
| --- | --- |
| `cpu` (default) | `quay.io/docling-project/docling-serve-cpu` |
| `base` | `quay.io/docling-project/docling-serve` |
| `cu128` | `quay.io/docling-project/docling-serve-cu128` |
| `cu130` | `quay.io/docling-project/docling-serve-cu130` |

The tag is `docling_serve_version`: `vK.L.M`, or `latest` for the `cpu` and
`base` variants only (upstream publishes the CUDA variants under version tags
only). To change either, edit the `image:` line and commit.

## Building the `docling-serve` image locally

To use a non-default build of the `docling-serve` image for your
deployment, create a `Dockerfile` in this directory, e.g.:

```Dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE=quay.io/docling-project/docling-serve-cpu:vK.L.M
FROM ${BASE_IMAGE}
```

and add the needful additional steps.

Then comment out or remove the `image:` stanza for the `docling-serve`
service, replacing it with a `build:` stanza which fits your needs.  E.g.:

```yaml
  docling-serve:
    # image: ...

    build:
      context: docling-serve
      dockerfile: Dockerfile
    # Override ARG here if needed
    # args:
    #   BASE_IMAGE: "quay.io/docling-project/docling-serve-cpu:vK.L.M"
```
