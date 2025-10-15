# -*- coding: utf-8 -*-
"""
getdf.py (versão ajustada)
Lê links do arquivo demo.txt, coleta dados de https://www.dfimoveis.com.br/imovel/impressao/{ID}
e grava na tabela dfdb.imoveis_df (MySQL).

pip install requests beautifulsoup4 lxml pymysql python-dateutil

Esse script foi ajustado para:
 - Pegar os dados dos imoveis do Distrito Federal. 
 - O tipo de negocio (venda ou aluguel) é determinado pela presença dos campos "Valor do imóvel venda" ou "Valor do imóvel aluguel".
 - O campo "Metragem" mantém o formato original (ex: "94,00 m²").
 - Se não funcionar, chama o Juca
"""

import os
import re
import time
import sys
from datetime import datetime
from dateutil import tz
import pymysql
import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
# Deixei o script do mysql no arquivo schema_dfdb.sql
MYSQL_HOST = "127.0.0.1"
MYSQL_USER = "root"
MYSQL_PASS = ""
MYSQL_DB   = "dfdb"
MYSQL_PORT = 3306
INPUT_FILE = "demo.txt" 

REQUEST_TIMEOUT = 25
RATE_LIMIT_SLEEP = 1.0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

# =========================
# FUNÇÕES AUXILIARES
# =========================
def br_now_str():
    tz_br = tz.gettz("America/Sao_Paulo")
    return datetime.now(tz_br).strftime("%Y-%m-%d %H:%M:%S")

def extract_id_from_url(url: str):
    m = re.search(r"/imovel/impressao/(\d+)", url)
    return int(m.group(1)) if m else None

def clean_money_to_str(val: str | None) -> str | None:
    """Remove apenas R$ e espaços extras, mantém o formato original."""
    if not val:
        return None
    s = val.replace("R$", "").strip()
    return re.sub(r"\s+", " ", s)

def clean_area_to_str(val: str | None) -> str | None:
    """Mantém o formato original (ex: '94,00 m²')."""
    if not val:
        return None
    return val.strip()

def get_text(el):
    return el.get_text(strip=True) if el else ""

def find_td_value_by_label(soup: BeautifulSoup, label: str) -> str | None:
    for td in soup.select("td.tlabel"):
        if get_text(td).lower() == label.lower():
            nxt = td.find_next_sibling(["td"])
            if nxt:
                return get_text(nxt)
    return None

def parse_quartos_suite_vagas(soup: BeautifulSoup):
    quartos = suites = vagas = None
    table = soup.select_one("table.caracteristicas")
    if not table:
        return quartos, suites, vagas

    tds = table.select("td")
    i = 0
    while i < len(tds) - 1:
        label = get_text(tds[i]).lower()
        val = get_text(tds[i + 1])
        if "quarto" in label:
            quartos = int(val) if val.isdigit() else None
        elif "suite" in label or "suíte" in label:
            suites = int(val) if val.isdigit() else None
        elif "garagem" in label or "vaga" in label:
            vagas = int(val) if val.isdigit() else None
        i += 2
    return quartos, suites, vagas

def parse_valor_e_negocio(soup: BeautifulSoup):
    valor = None
    tipo_negocio = None
    for td in soup.select("td.tlabel"):
        label = get_text(td)
        if label.lower() == "valor do imóvel venda":
            nxt = td.find_next_sibling("td")
            if nxt:
                strong = nxt.find("strong")
                txt = get_text(strong or nxt)
                valor = clean_money_to_str(txt)
                tipo_negocio = "Venda"
                return valor, tipo_negocio
        if label.lower() == "valor do imóvel aluguel":
            nxt = td.find_next_sibling("td")
            if nxt:
                strong = nxt.find("strong")
                txt = get_text(strong or nxt)
                valor = clean_money_to_str(txt)
                tipo_negocio = "Aluguel"
                return valor, tipo_negocio
    return valor, tipo_negocio

def parse_valor_m2_e_area(soup: BeautifulSoup):
    valor_m2 = None
    metragem = None
    for td in soup.select("td.tlabel"):
        if get_text(td).lower() == "valor do m²":
            vtd = td.find_next_sibling("td")
            if vtd:
                valor_m2 = clean_money_to_str(get_text(vtd))
        if get_text(td).lower() == "área privativa":
            atd = td.find_next_sibling("td")
            if atd:
                metragem = clean_area_to_str(get_text(atd))
    return valor_m2, metragem

def build_titulo(tipo, bairro, cidade, endereco):
    base = []
    if tipo: base.append(tipo)
    if endereco: base.append(endereco)
    if bairro and cidade:
        base.append(f"{bairro} - {cidade}")
    elif bairro:
        base.append(bairro)
    elif cidade:
        base.append(cidade)
    titulo = " | ".join(base) if base else "Imóvel"
    return titulo[:200]

def fetch_html(url: str):
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None

def insert_or_update(conn, row):
    sql = """
    INSERT INTO imoveis_df
      (ID, CIDADE, BAIRRO, endereco, tipo, Titulo, Metragem, QUARTOS, SUITES, VAGAS, VALOR, tipo_negocio, valor_m2, data_da_busca)
    VALUES
      (%(ID)s, %(CIDADE)s, %(BAIRRO)s, %(endereco)s, %(tipo)s, %(Titulo)s, %(Metragem)s, %(QUARTOS)s, %(SUITES)s, %(VAGAS)s, %(VALOR)s, %(tipo_negocio)s, %(valor_m2)s, %(data_da_busca)s)
    ON DUPLICATE KEY UPDATE
      CIDADE=VALUES(CIDADE),
      BAIRRO=VALUES(BAIRRO),
      endereco=VALUES(endereco),
      tipo=VALUES(tipo),
      Titulo=VALUES(Titulo),
      Metragem=VALUES(Metragem),
      QUARTOS=VALUES(QUARTOS),
      SUITES=VALUES(SUITES),
      VAGAS=VALUES(VAGAS),
      VALOR=VALUES(VALOR),
      tipo_negocio=VALUES(tipo_negocio),
      valor_m2=VALUES(valor_m2),
      data_da_busca=VALUES(data_da_busca)
    """
    with conn.cursor() as cur:
        cur.execute(sql, row)
    conn.commit()

def parse_page(url: str):
    page_id = extract_id_from_url(url)
    if not page_id:
        print(f"[WARN] ID não encontrado: {url}")
        return None

    html = fetch_html(url)
    if not html:
        print(f"[WARN] Falha ao baixar HTML: {url}")
        return None

    soup = BeautifulSoup(html, "lxml")

    tipo = find_td_value_by_label(soup, "Tipo")
    endereco = find_td_value_by_label(soup, "Endereço")
    bairro = find_td_value_by_label(soup, "Bairro")
    cidade = find_td_value_by_label(soup, "Cidade")
    quartos, suites, vagas = parse_quartos_suite_vagas(soup)
    valor, tipo_negocio = parse_valor_e_negocio(soup)
    valor_m2, metragem = parse_valor_m2_e_area(soup)
    titulo = build_titulo(tipo, bairro, cidade, endereco)

    row = {
        "ID": page_id,
        "CIDADE": cidade or "N/D",
        "BAIRRO": bairro or "N/D",
        "endereco": endereco,
        "tipo": tipo,
        "Titulo": titulo,
        "Metragem": metragem,
        "QUARTOS": quartos,
        "SUITES": suites,
        "VAGAS": vagas,
        "VALOR": valor,
        "tipo_negocio": tipo_negocio,
        "valor_m2": valor_m2,
        "data_da_busca": br_now_str(),
    }
    return row

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[ERRO] Arquivo '{INPUT_FILE}' não encontrado.")
        sys.exit(1)

    conn = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        port=MYSQL_PORT,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )

    total, ok = 0, 0
    with conn:
        for line in open(INPUT_FILE, encoding="utf-8"):
            url = line.strip()
            if not url:
                continue
            total += 1
            print(f"[INFO] Buscando: {url}")
            try:
                data = parse_page(url)
                if data:
                    insert_or_update(conn, data)
                    ok += 1
                    print(f"[OK] ID {data['ID']} gravado.")
                else:
                    print("[WARN] Nenhum dado.")
            except Exception as e:
                print(f"[ERRO] {url}: {e}")
            time.sleep(RATE_LIMIT_SLEEP)
    print(f"[FINALIZADO] {ok}/{total} registros salvos.")

if __name__ == "__main__":
    main()
