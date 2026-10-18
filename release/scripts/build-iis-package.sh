#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
npm ci
npm run build
echo "IIS公開用パッケージ: artifacts/enterprise-xml-analyzer-iis"
