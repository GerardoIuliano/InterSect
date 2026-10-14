#!/bin/bash

# -------------------------------
# switch-solc-verisol.sh
# -------------------------------
# Uso: ./switch-solc-verisol.sh 0.6.12
# -------------------------------

# Controllo argomento
if [ $# -ne 1 ]; then
    echo "Use: $0 <versione-solc>"
    echo "Example: $0 0.6.12"
    exit 1
fi

VERSION=$1
VERISOL_LINK="/Users/gerardo/Desktop/Formal_Verification_UNISA_UCL/verisol/bin/Debug/solc"
ARTIFACT="$HOME/.solc-select/artifacts/solc-$VERSION/solc-$VERSION"

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

# 2️⃣ Installazione versione desiderata con solc-select
echo "Installing solc $VERSION with solc-select..."
# solc-select install "$VERSION"
solc-select use "$VERSION"

# 3️⃣ Creo link simbolico per VeriSol
echo "Creating symbolic link for VeriSol..."
mkdir -p "$(dirname "$VERISOL_LINK")"
ln -s "$ARTIFACT" "$VERISOL_LINK"

# 4️⃣ Verifica
echo "VeriSol is using:"
"$VERISOL_LINK" --version
