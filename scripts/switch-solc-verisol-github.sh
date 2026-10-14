#!/bin/bash

# -------------------------------
# switch-solc-verisol.sh
# -------------------------------
# Uso: ./switch-solc-verisol.sh 0.6.12
# -------------------------------

if [ $# -ne 1 ]; then
    echo "Use: $0 <versione-solc>"
    echo "Example: $0 0.6.12"
    exit 1
fi

VERSION=$1
VERISOL_LINK="/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/verisol/bin/Debug/solc"
ARTIFACT="$HOME/.solc-select/artifacts/solc-$VERSION/solc-$VERSION"

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" == "arm64" ]; then
    SOLC_BINARY="solc-macos-aarch64"
else
    SOLC_BINARY="solc-macos-amd64"
fi

DOWNLOAD_URL="https://github.com/ethereum/solidity/releases/download/v$VERSION/$SOLC_BINARY"

# Controllo se il link simbolico esiste e punta già alla versione desiderata
if [ -L "$VERISOL_LINK" ]; then
    CURRENT_TARGET=$(readlink "$VERISOL_LINK")
    if [ "$CURRENT_TARGET" == "$ARTIFACT" ]; then
        echo "Current version: $VERSION. No change needed."
        exit 0
    fi
fi

# 1️⃣ Rimuovo link simbolico esistente
if [ -L "$VERISOL_LINK" ]; then
    echo "Removing symbolic link: $VERISOL_LINK"
    rm "$VERISOL_LINK"
fi

# 2️⃣ Download da GitHub releases (solo se non già presente)
if [ ! -f "$ARTIFACT" ]; then
    echo "Downloading solc $VERSION from GitHub..."
    mkdir -p "$(dirname "$ARTIFACT")"
    curl -L "$DOWNLOAD_URL" -o "$ARTIFACT"
    if [ $? -ne 0 ]; then
        echo "Error: download failed from $DOWNLOAD_URL"
        exit 1
    fi
    chmod +x "$ARTIFACT"
    echo "Download complete: $ARTIFACT"
else
    echo "solc $VERSION already present, skipping download."
fi

# 3️⃣ Creo link simbolico per VeriSol
echo "Creating symbolic link for VeriSol..."
mkdir -p "$(dirname "$VERISOL_LINK")"
ln -s "$ARTIFACT" "$VERISOL_LINK"

# 4️⃣ Verifica
echo "VeriSol is using:"
"$VERISOL_LINK" --version