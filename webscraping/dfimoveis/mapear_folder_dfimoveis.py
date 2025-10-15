# -*- coding: utf-8 -*-
"""
mapear_folder_dfimoveis.py
--------------------------
Percorre sequencialmente um intervalo de IDs do DFImóveis no endpoint:
  https://www.dfimoveis.com.br/imovel/impressao/{id}

Para cada URL:
- Se a página contém o heading H1 com classe "titulo" e o texto "Folder do Imóvel",
  grava a URL em url_validas.txt
- Caso contrário, grava a URL em url_invalidas.txt

Uso:
  pip install requests beautifulsoup4
  python mapear_folder_dfimoveis.py --inicio 1240957 --fim 1000
  # (varre: 100000, 99999, 99998, ..., 95000)

Opções:
  --inicio <int>      ID inicial (inclusive)
  --fim <int>         ID final (inclusive)
  --saida <dir>       Diretório de saída dos .txt (padrão: ./)
  --timeout <int>     Timeout em segundos para requests (padrão: 15)
  --sleep <float>     Pausa entre requisições em segundos (padrão: 0.1)
  --ua <str>          User-Agent customizado
  --resumir           Continua o processamento sem duplicar linhas se os .txt já existem

Notas:
- O script é SEQUENCIAL por especificação.
- Tolerante a erros HTTP: 404/500/timeout contam como inválidas.
- Detecção do texto é "case-insensitive", ignora acentos e espaços extras.
"""

import os
import re
import time
import argparse
import unicodedata
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.dfimoveis.com.br/imovel/impressao/{id}"
UA_DEFAULT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

def strip_accents_lower(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s, flags=re.UNICODE).strip().lower()
    return s

def has_folder_heading(html: str) -> bool:
    """
    Verifica se existe <h1 class="titulo"> contendo "Folder do Imóvel".
    Ignora acentos/maiúsculas/minúsculas e espaços.
    """
    soup = BeautifulSoup(html, "html.parser")
    for h1 in soup.find_all("h1", class_="titulo"):
        texto = strip_accents_lower(h1.get_text(" ", strip=True))
        if "folder do imovel" in texto:
            return True
    return False

def fetch_html(url: str, timeout: int, ua: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
        # Considera 200 somente; outros status => inválida
        if resp.status_code == 200 and resp.text:
            return resp.text
        return None
    except requests.RequestException:
        return None

def main():
    ap = argparse.ArgumentParser(description="Mapeia URLs com 'Folder do Imóvel' no DFImóveis.")
    ap.add_argument("--inicio", type=int, required=True, help="ID inicial (inclusive).")
    ap.add_argument("--fim", type=int, required=True, help="ID final (inclusive).")
    ap.add_argument("--saida", default=".", help="Diretório para os arquivos url_validas.txt e url_invalidas.txt")
    ap.add_argument("--timeout", type=int, default=15, help="Timeout por request em segundos (padrão: 15).")
    ap.add_argument("--sleep", type=float, default=0.1, help="Pausa entre requests (padrão: 0.1s).")
    ap.add_argument("--ua", default=UA_DEFAULT, help="User-Agent HTTP.")
    ap.add_argument("--resumir", action="store_true", help="Evita duplicatas lendo arquivos existentes.")
    args = ap.parse_args()

    saida_dir = os.path.abspath(args.saida)
    os.makedirs(saida_dir, exist_ok=True)

    path_validas = os.path.join(saida_dir, "url_validas.txt")
    path_invalidas = os.path.join(saida_dir, "url_invalidas.txt")

    # Conjuntos para resumir/evitar duplicatas
    ja_validas = set()
    ja_invalidas = set()
    if args.resumir:
        if os.path.exists(path_validas):
            with open(path_validas, "r", encoding="utf-8") as f:
                ja_validas = set(x.strip() for x in f if x.strip())
        if os.path.exists(path_invalidas):
            with open(path_invalidas, "r", encoding="utf-8") as f:
                ja_invalidas = set(x.strip() for x in f if x.strip())

    total = 0
    encontrados = 0
    invalidos = 0

    # Ordem sequencial (decrescente se inicio > fim; crescente caso contrário)
    step = -1 if args.inicio >= args.fim else 1
    rng = range(args.inicio, args.fim + step, step)

    print(f"Saída: {saida_dir}")
    print(f"Processando IDs de {args.inicio} até {args.fim} (passo {step}) ...")

    with open(path_validas, "a", encoding="utf-8") as f_ok, \
         open(path_invalidas, "a", encoding="utf-8") as f_bad:

        for i in rng:
            url = BASE_URL.format(id=i)
            total += 1

            # Resume: já processada?
            if args.resumir and (url in ja_validas or url in ja_invalidas):
                continue

            html = fetch_html(url, timeout=args.timeout, ua=args.ua)
            if html and has_folder_heading(html):
                f_ok.write(url + "\n")
                f_ok.flush()
                encontrados += 1
                status = "OK"
            else:
                f_bad.write(url + "\n")
                f_bad.flush()
                invalidos += 1
                status = "NOK"

            # Log leve
            if total % 100 == 0:
                print(f"[{total}] últimos 100: válidas+{encontrados} | inválidas+{invalidos} -> {url} [{status}]")

            time.sleep(args.sleep)

    print(f"Concluído. Total: {total} | Válidas: {encontrados} | Inválidas: {invalidos}")
    print(f"- url_validas.txt:   {path_validas}")
    print(f"- url_invalidas.txt: {path_invalidas}")

if __name__ == "__main__":
    main()
