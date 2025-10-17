# -*- coding: utf-8 -*-
"""
consultas_imoveis.py (precisa + tipo/endereco + busca por endereço)
- Filtros por cidade, bairro, endereco (tokenizado), tipo, quartos, suites, vagas, metragem
- Lista resultados com endereco
- Estimativa baseada em comparáveis (mesmos filtros, inclusive endereco se informado)
"""

import mysql.connector
import re
from statistics import mean

# ===== Config MySQL =====
config = {
    "user": "root",
    "password": "",
    "host": "localhost",
    "database": "dfdb",
    "port": 3306
}

def conectar():
    return mysql.connector.connect(**config)

# ---------- Parsers ----------
def parse_metragem_str_to_float(m_str: str):
    if m_str is None:
        return None
    s = str(m_str).strip().lower().replace("m²", "")
    s = re.sub(r"[^\d,\.]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def parse_valor_str_to_float(v_str: str):
    if v_str is None:
        return None
    s = re.sub(r"[^\d]", "", str(v_str))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

def arredondar_milhar(v: float) -> float:
    return round(v / 1000.0) * 1000.0

def parse_metragem_param(valor):
    if not valor or valor == "*":
        return None
    s = str(valor).strip().lower().replace("m²", "")
    s = re.sub(r"[^\d\-,]", "", s)
    if "-" in s:
        a, b = s.split("-", 1)
        try:
            return (float(a), float(b))
        except:
            return None
    try:
        return float(s)
    except:
        return None

# ---------- Helpers de LIKE ----------
def tokens_from_text(texto: str):
    """
    Quebra o endereço em tokens alfanuméricos úteis para LIKE.
    Ex.: 'QS 5 Rua 400 - Residencial Montana' -> ['QS', '5', 'RUA', '400', 'RESIDENCIAL', 'MONTANA']
    """
    toks = re.findall(r"[A-Za-z0-9]+", str(texto or "").upper())
    return [t for t in toks if t]

def apply_like_tokens(sql_base: str, params: list, campo: str, texto: str):
    """
    Para cada token, adiciona 'AND campo LIKE %token%' (AND entre os tokens).
    """
    toks = tokens_from_text(texto)
    for t in toks:
        sql_base += f" AND {campo} LIKE %s"
        params.append(f"%{t}%")
    return sql_base, params

# ---------- Cálculo robusto do m² ----------
def media_m2_comparaveis(cursor,
                         bairro: str | None,
                         cidade: str | None,
                         endereco: str | None,
                         quartos: int | None,
                         suites: int | None,
                         vagas: int | None,
                         tipo: str | None,
                         metragem_alvo: float | None,
                         metragem_intervalo: tuple[float, float] | None,
                         tipo_negocio: str = "Venda",
                         tolerancia_pct: float = 0.10,
                         trim_quantil: float = 0.10,
                         comparables_limit: int = 2000,
                         min_amostra_local: int = 5):  # NOVO parâmetro
    """
    Retorna (valor_m2_robusto, n_usados, nivel) onde nivel ∈ {'endereco','bairro','cidade'}
    Estratégia:
      1) Tenta por ENDEREÇO (se informado); se <min_amostra_local comps válidos, afrouxa p/ BAIRRO; se <3, cai p/ CIDADE.
      2) Filtros por Q/S/V e TIPO
      3) Faixa de metragem (alvo ± tolerância) ou intervalo informado
      4) Remoção de outliers por trim de quantis
      5) Média ponderada por proximidade de metragem
    """
    def montar(nivel: str):
        base = "SELECT ID, Metragem, VALOR FROM imoveis_df WHERE 1=1"
        params = []
        if nivel == "endereco" and endereco:
            base, params = apply_like_tokens(base, params, "endereco", endereco)
        elif nivel == "bairro" and bairro:
            base += " AND BAIRRO LIKE %s"; params.append(f"%{bairro}%")
        elif nivel == "cidade" and cidade:
            base += " AND CIDADE LIKE %s"; params.append(f"%{cidade}%")
        else:
            return None, None

        if tipo:
            base += " AND tipo LIKE %s"; params.append(f"%{tipo}%")
        if quartos is not None:
            base += " AND QUARTOS = %s"; params.append(quartos)
        if suites is not None:
            base += " AND SUITES = %s"; params.append(suites)
        if vagas is not None:
            base += " AND VAGAS = %s"; params.append(vagas)
        if tipo_negocio:
            base += " AND tipo_negocio LIKE %s"; params.append(f"%{tipo_negocio}%")

        if metragem_intervalo and len(metragem_intervalo) == 2:
            a, b = metragem_intervalo
            base += " AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') AS DECIMAL(10,2)) BETWEEN %s AND %s"
            params.extend([a, b])
        elif metragem_alvo:
            a = metragem_alvo * (1 - tolerancia_pct)
            b = metragem_alvo * (1 + tolerancia_pct)
            base += " AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') AS DECIMAL(10,2)) BETWEEN %s AND %s"
            params.extend([a, b])

        base += f" LIMIT {comparables_limit}"
        return base, params

    # 1) Endereço (se fornecido)
    nivel_ordem = []
    if endereco:
        nivel_ordem.append("endereco")
    nivel_ordem += ["bairro", "cidade"]

    rows = []
    nivel_usado = None
    for nv in nivel_ordem:
        sqlx, parx = montar(nv)
        if not sqlx:
            continue
        cursor.execute(sqlx, parx)
        rows = cursor.fetchall()
        # Converte
        comps = []
        for r in rows:
            m = parse_metragem_str_to_float(r["Metragem"])
            v = parse_valor_str_to_float(r["VALOR"])
            if m and m > 0 and v and v > 0:
                comps.append((m, v, v / m, r["ID"]))
        # Se for endereço, exige min_amostra_local, senão exige pelo menos 3
        if (nv == "endereco" and len(comps) >= min_amostra_local) or (nv != "endereco" and len(comps) >= 3):
            nivel_usado = nv
            rows = [r for r in rows if r["ID"] in [c[3] for c in comps]]
            break

    if not rows:
        return None, 0, (nivel_usado or "cidade")

    parsed = []
    for r in rows:
        m = parse_metragem_str_to_float(r["Metragem"])
        v = parse_valor_str_to_float(r["VALOR"])
        if m and m > 0 and v and v > 0:
            parsed.append((m, v, v / m, r["ID"]))

    if len(parsed) < 3:
        return None, len(parsed), (nivel_usado or "cidade")

    # trim outliers
    per_m2 = sorted(x[2] for x in parsed)
    if len(per_m2) > 10:
        ql = per_m2[int(len(per_m2) * trim_quantil)]
        qh = per_m2[int(len(per_m2) * (1 - trim_quantil)) - 1]
        parsed_trim = [x for x in parsed if ql <= x[2] <= qh] or parsed
    else:
        parsed_trim = parsed

    # Mostra os comparáveis usados
    print("\n🔎 Comparáveis usados no cálculo:")
    for m, v, pm2, id_ in parsed_trim:
        print(f"ID: {id_} | {m:.2f} m² | R$ {v:,.0f} | R$ {pm2:,.2f}/m²")

    # ponderação por proximidade
    if metragem_alvo:
        pesos, valores = [], []
        for (m, v, pm2, _) in parsed_trim:
            dist = abs(m - metragem_alvo)
            peso = 1.0 / (1.0 + dist)
            pesos.append(peso); valores.append(pm2)
        valor_m2 = sum(p * x for p, x in zip(pesos, valores)) / sum(pesos)
    else:
        valor_m2 = mean(pm2 for (_, _, pm2, _) in parsed_trim)

    return valor_m2, len(parsed_trim), (nivel_usado or "cidade")

# ---------- Busca + Estimativa ----------
def buscar_imoveis(metragem=None, quartos=None, suites=None, vagas=None,
                   cidade=None, bairro=None, endereco=None, tipo=None, limite=20,
                   estado_conservacao="Padrão",
                   metragem_para_estimativa=None,
                   tolerancia_m2_pct=0.10,
                   tipo_negocio="Venda"):  # NOVO
    conn = conectar()
    cursor = conn.cursor(dictionary=True)

    sql = "SELECT * FROM imoveis_df WHERE 1=1"
    params = []

    pm = parse_metragem_param(metragem)
    if isinstance(pm, tuple):
        sql += " AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') AS DECIMAL(10,2)) BETWEEN %s AND %s"
        params.extend([pm[0], pm[1]])
    elif isinstance(pm, float):
        sql += " AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') AS DECIMAL(10,2)) >= %s"
        params.append(pm)

    if quartos is not None:
        sql += " AND QUARTOS = %s"; params.append(quartos)
    if suites is not None:
        sql += " AND SUITES = %s"; params.append(suites)
    if vagas is not None:
        sql += " AND VAGAS = %s"; params.append(vagas)
    if cidade and cidade != "*":
        sql += " AND CIDADE LIKE %s"; params.append(f"%{cidade}%")
    if bairro and bairro != "*":
        sql += " AND BAIRRO LIKE %s"; params.append(f"%{bairro}%")
    if tipo:
        sql += " AND tipo LIKE %s"; params.append(f"%{tipo}%")
    if endereco:
        # aplica tokens no SELECT de listagem
        sql, params = apply_like_tokens(sql, params, "endereco", endereco)
    if tipo_negocio:
        sql += " AND tipo_negocio LIKE %s"; params.append(f"%{tipo_negocio}%")

    sql += " ORDER BY CAST(REPLACE(REPLACE(VALOR, '.', ''), ',', '') AS UNSIGNED) DESC LIMIT %s"
    params.append(limite)

    cursor.execute(sql, params)
    resultados = cursor.fetchall()

    # Listagem
    if not resultados:
        print("⚠️ Nenhum imóvel encontrado com esses filtros.")
    else:
        print(f"\n✅ {len(resultados)} resultado(s) encontrado(s):\n")
        for r in resultados:
            print(f"ID: {r['ID']}")
            print(f"🏙️ {r['CIDADE']} — {r['BAIRRO']}")
            print(f"📍 {r.get('endereco','') or '-'}")
            print(f"🏢 Tipo: {r.get('tipo','') or '-'}")
            print(f"📏 {r['Metragem']} | 🛏️ {r['QUARTOS']}Q | 🛁 {r['SUITES']}S | 🚗 {r['VAGAS']}V")
            print(f"💰 Valor: {r['VALOR']}")
            print(f"📌 Título: {r['Titulo']}")
            print("-" * 60)

    # Estimativa
    if isinstance(metragem_para_estimativa, (int, float)):
        metragem_alvo = float(metragem_para_estimativa)
    else:
        if isinstance(pm, float):
            metragem_alvo = pm
        elif resultados:
            metragem_alvo = parse_metragem_str_to_float(resultados[0].get("Metragem"))
        else:
            metragem_alvo = None

    valor_m2, n_usados, nivel = media_m2_comparaveis(
        cursor,
        bairro=bairro, cidade=cidade, endereco=endereco,
        quartos=quartos, suites=suites, vagas=vagas, tipo=tipo,
        metragem_alvo=metragem_alvo,
        metragem_intervalo=(pm if isinstance(pm, tuple) else None),
        tipo_negocio=tipo_negocio,  # NOVO
        tolerancia_pct=tolerancia_m2_pct, trim_quantil=0.10, comparables_limit=2000
    )

    if valor_m2 and metragem_alvo:
        print(
            f"\n📐 Base do cálculo: m² ponderado no {nivel} "
            f"(comparáveis: {n_usados}, tol ±{int(tolerancia_m2_pct*100)}%): "
            f"R$ {valor_m2:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )

        valor_base = metragem_alvo * valor_m2

        estado = (estado_conservacao or "Padrão").strip().lower()
        ajuste_pct = 0.0
        desc_estado = "em bom estado de conservação"
        if estado == "reformado":
            ajuste_pct = 0.10; desc_estado = "em excelente estado de conservação"
        elif estado == "original":
            ajuste_pct = -0.10; desc_estado = "necessitando de reforma/manutenção"

        valor_ajustado = valor_base * (1 + ajuste_pct)
        valor_estimado = arredondar_milhar(valor_ajustado)
        faixa_min = valor_estimado * 0.95
        faixa_max = valor_estimado * 1.05

        def fmt(v): return f"R$ {v:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

        print("\n🧮 Estimativa de Valor de Mercado")
        print(f"- Metragem alvo: {metragem_alvo:.2f} m²")
        print(f"- Valor base (m² médio x metragem): {fmt(valor_base)}")
        print(f"- Ajuste por estado ({desc_estado}): {('+' if ajuste_pct>=0 else '')}{int(ajuste_pct*100)}%")
        print(f"- Valor estimado (arredondado ao milhar): {fmt(valor_estimado)}")
        print(f"- Faixa de negociação (±5%): {fmt(faixa_min)} a {fmt(faixa_max)}")
    else:
        print("\n⚠️ Amostra insuficiente para calcular valor_m2 com os comparáveis definidos.")

    cursor.close()
    conn.close()

# ===== Exemplo =====
if __name__ == "__main__":
    # Ex.: endereço padronizado (tokens "QS", "5", "RUA", "400" vão ser aplicados em ANDs)
    buscar_imoveis(
        cidade="CEILANDIA",
        bairro="CEILANDIA SUL",
        endereco="QNM 25",
        tipo="CASA",
        limite=10,
        quartos=3,        
        vagas=2,
        suites=0,
        metragem="200-250",
        estado_conservacao="original",
        tolerancia_m2_pct=0.10,
        tipo_negocio="Venda"  
    )
