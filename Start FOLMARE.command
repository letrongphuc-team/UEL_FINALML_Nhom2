#!/bin/zsh
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo "Chua co moi truong Python. Vui long thuc hien cac buoc trong README.md."
  read "?Nhan Enter de dong..."
  exit 1
fi
.venv/bin/python -B main.py
