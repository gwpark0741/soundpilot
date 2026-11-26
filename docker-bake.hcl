# Builds the UI image, the server image and the single RunPod image in one BuildKit graph:
# the RunPod image reads the other two as build stages (no docker save/load in between), and with
# CACHE set every layer can come from the registry cache, so only what changed is rebuilt.
#   BUILDX_BUILDER=audyssey-builder docker buildx bake --load runpod            (sv3)
#   CACHE=ghcr.io/gwpark0741/soundpilot:buildcache docker buildx bake runpod    (with registry cache)

variable "IMAGE" {
  default = "soundpilot"
}

variable "TAG" {
  default = "dev"
}

# Commit for the org.opencontainers.image.revision label; empty leaves the label out.
variable "REVISION" {
  default = ""
}

# Audyssey tools verified with SoundPilot end to end on 2026-11-23. Change only after re-verifying.
variable "TOOLS_IMAGE" {
  default = "ghcr.io/gwpark0741/soundpilot-tools:demo-1117"
}

# Registry cache prefix; each target uses "<CACHE>-<target>". Empty: the builder's local cache only.
variable "CACHE" {
  default = ""
}

function "cache_from" {
  params = [name]
  result = CACHE == "" ? [] : ["type=registry,ref=${CACHE}-${name}"]
}

# mode=max keeps the builder stages too (uv sync, npm ci), which the final images do not contain.
function "cache_to" {
  params = [name, mode]
  result = CACHE == "" ? [] : ["type=registry,ref=${CACHE}-${name},mode=${mode}"]
}

group "default" {
  targets = ["runpod"]
}

target "ui" {
  dockerfile = "Dockerfile"
  cache-from = cache_from("ui")
  cache-to   = cache_to("ui", "max")
}

target "server" {
  dockerfile = "server/Dockerfile"
  cache-from = cache_from("server")
  cache-to   = cache_to("server", "max")
}

target "runpod" {
  dockerfile = "Dockerfile.runpod"
  # Keys are Dockerfile.runpod's default FROM names.
  contexts = {
    "v2a-inspect:demo"         = "target:ui"
    "v2a-inspect-server:demo"  = "target:server"
    "audyssey-tools:demo-1117" = "docker-image://${TOOLS_IMAGE}"
  }
  tags       = ["${IMAGE}:${TAG}"]
  labels     = REVISION == "" ? {} : { "org.opencontainers.image.revision" = REVISION }
  cache-from = cache_from("runpod")
  cache-to   = cache_to("runpod", "min")
}
