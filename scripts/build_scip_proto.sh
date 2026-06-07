#!/usr/bin/env bash
# Generate Python bindings for the SCIP protobuf schema.
# Output: src/kg/indexers/_scip_pb2.py
#
# Requires: protoc on PATH, curl, python with `grpc_tools` (pip install grpcio-tools).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out_dir="$here/../src/kg/indexers"
proto_dir="$(mktemp -d)"
trap 'rm -rf "$proto_dir"' EXIT

curl -sSL \
  https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto \
  -o "$proto_dir/scip.proto"

python -m grpc_tools.protoc \
  --proto_path="$proto_dir" \
  --python_out="$out_dir" \
  "$proto_dir/scip.proto"

# Rename to a private module name and fix the import.
mv "$out_dir/scip_pb2.py" "$out_dir/_scip_pb2.py"

echo "Generated: $out_dir/_scip_pb2.py"
