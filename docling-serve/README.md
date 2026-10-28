## `docling-serve` version compatibility

The `docling-serve` image version is loosely coupled in this stack
to `haiku-rag`, and only transitively to `soliplex`.

As with any dependency, it has over time had behavioral quirks and
changes which affect the quality of the `haiku-ingester`'s output, or even
cause it to fail outright for some documents.  At such times,
the `haiku-rag-slim` version may pin the `docling-*` packages to
versions known to work.

The process outlined in `../docs/upgrades/index`
for keeping `haiku-rag-slim` in sync with `soliplex` doesn't really cover
this problem directly.

## Using the stock `docling-serve` image

As generated, the `docling-serve` service uses an upstream image as published,
without a local build. E.g.:

```yaml
  docling-serve:
    image: quay.io/docling-project/docling-serve-cpu:vK.L.M

    #build:
    #  context: haiku.rag
    #  dockerfile: Dockerfile
```

The `-cpu` variant in that image name may be absent, if disabled at generation
time.

## Building the 'docling-serve' image locally

To use a non-default build of the `docling-serve` image for your
deployment, create a `Dockerfile` in this directory, e.g.:

```Dockerfile
# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ghcr.io/docling-project/docling-serve-cpu:vK.L.M
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
    #   BASE_IMAGE: "quay.io/ggozad/haiku.rag-slim:U.V.W"
```
