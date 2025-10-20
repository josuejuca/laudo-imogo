# -*- coding: utf-8 -*-
import re
from statistics import mean
from typing import Optional, Tuple, List
import time
from collections import defaultdict
from typing import Dict, Set, Any
from fastapi import Path

import mysql.connector
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# =========================
# Config MySQL
# =========================
config = {
    "user": "",
    "password": "",
    "host": "",
    "database": "quadr767_laudo-db",
    "port": 3306
}

def conectar():
    return mysql.connector.connect(**config)

# =========================
# Utils / Parsers
# =========================
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

def parse_metragem_param(valor) -> Optional[Tuple[float, float] or float]:
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

# ---------- LIKE helpers ----------
def tokens_from_text(texto: str) -> List[str]:
    toks = re.findall(r"[A-Za-z0-9]+", str(texto or "").upper())
    return [t for t in toks if t]

def apply_like_tokens(sql_base: str, params: list, campo: str, texto: str):
    toks = tokens_from_text(texto)
    for t in toks:
        sql_base += f" AND {campo} LIKE %s"
        params.append(f"%{t}%")
    return sql_base, params

# =========================
# Núcleo: cálculo do m² por comparáveis
# =========================
def media_m2_comparaveis(cursor,
                         bairro: Optional[str],
                         cidade: Optional[str],
                         endereco: Optional[str],
                         quartos: Optional[int],
                         suites: Optional[int],
                         vagas: Optional[int],
                         tipo: Optional[str],
                         metragem_alvo: Optional[float],
                         metragem_intervalo: Optional[Tuple[float, float]],
                         tipo_negocio: str = "Venda",
                         tolerancia_pct: float = 0.10,
                         trim_quantil: float = 0.10,
                         comparables_limit: int = 2000,
                         min_amostra_local: int = 5):
    """
    Retorna (valor_m2_robusto, n_usados, nivel, parsed_trim)
    nivel ∈ {'endereco','bairro','cidade'}
    parsed_trim = lista [(m, v, pm2, id)]
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
            base += (" AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') "
                     "AS DECIMAL(10,2)) BETWEEN %s AND %s")
            params.extend([a, b])
        elif metragem_alvo:
            a = metragem_alvo * (1 - tolerancia_pct)
            b = metragem_alvo * (1 + tolerancia_pct)
            base += (" AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') "
                     "AS DECIMAL(10,2)) BETWEEN %s AND %s")
            params.extend([a, b])

        base += f" LIMIT {comparables_limit}"
        return base, params

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

        comps = []
        for r in rows:
            m = parse_metragem_str_to_float(r["Metragem"])
            v = parse_valor_str_to_float(r["VALOR"])
            if m and m > 0 and v and v > 0:
                comps.append((m, v, v / m, r["ID"]))

        if (nv == "endereco" and len(comps) >= min_amostra_local) or (nv != "endereco" and len(comps) >= 3):
            nivel_usado = nv
            rows = [r for r in rows if r["ID"] in [c[3] for c in comps]]
            break

    if not rows:
        return None, 0, (nivel_usado or "cidade"), []

    parsed = []
    for r in rows:
        m = parse_metragem_str_to_float(r["Metragem"])
        v = parse_valor_str_to_float(r["VALOR"])
        if m and m > 0 and v and v > 0:
            parsed.append((m, v, v / m, r["ID"]))

    if len(parsed) < 3:
        return None, len(parsed), (nivel_usado or "cidade"), parsed

    # trim outliers
    per_m2 = sorted(x[2] for x in parsed)
    if len(per_m2) > 10:
        ql = per_m2[int(len(per_m2) * trim_quantil)]
        qh = per_m2[int(len(per_m2) * (1 - trim_quantil)) - 1]
        parsed_trim = [x for x in parsed if ql <= x[2] <= qh] or parsed
    else:
        parsed_trim = parsed

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

    return valor_m2, len(parsed_trim), (nivel_usado or "cidade"), parsed_trim

def fmt_brl(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    s = f"R$ {v:,.0f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

# =============== NOVO: buscar metragem do primeiro resultado (como seu script) ===============
def buscar_primeira_metragem(
    cidade: Optional[str], bairro: Optional[str], endereco: Optional[str], tipo: Optional[str],
    quartos: Optional[int], suites: Optional[int], vagas: Optional[int],
    tipo_negocio: str
) -> Optional[float]:
    conn = conectar()
    cur = conn.cursor(dictionary=True)
    try:
        sql = "SELECT Metragem FROM imoveis_df WHERE 1=1"
        params = []
        if cidade:
            sql += " AND CIDADE LIKE %s"; params.append(f"%{cidade}%")
        if bairro:
            sql += " AND BAIRRO LIKE %s"; params.append(f"%{bairro}%")
        if tipo:
            sql += " AND tipo LIKE %s"; params.append(f"%{tipo}%")
        if quartos is not None:
            sql += " AND QUARTOS = %s"; params.append(quartos)
        if suites is not None:
            sql += " AND SUITES = %s"; params.append(suites)
        if vagas is not None:
            sql += " AND VAGAS = %s"; params.append(vagas)
        if tipo_negocio:
            sql += " AND tipo_negocio LIKE %s"; params.append(f"%{tipo_negocio}%")
        if endereco:
            sql, params = apply_like_tokens(sql, params, "endereco", endereco)

        # mesmo sort do script (valor desc). Ajuste se necessário.
        sql += " ORDER BY CAST(REPLACE(REPLACE(VALOR, '.', ''), ',', '') AS UNSIGNED) DESC LIMIT 1"
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return None
        return parse_metragem_str_to_float(row.get("Metragem"))
    finally:
        cur.close()
        conn.close()

# =========================
# FastAPI
# =========================
app = FastAPI(title="API de Estimativa de Imóveis", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

@app.get("/api/laudo/estimativa")
def estimativa(
    cidade: Optional[str] = Query(None),
    bairro: Optional[str] = Query(None),
    endereco: Optional[str] = Query(None, description="Texto livre; tokenizado p/ LIKE AND"),
    tipo: Optional[str] = Query(None, description="Ex: CASA, APARTAMENTO"),
    limite: int = Query(20, ge=1, le=2000),

    quartos: Optional[int] = Query(None, ge=0),
    vagas: Optional[int] = Query(None, ge=0),
    suites: Optional[int] = Query(None, ge=0),

    metragem: Optional[str] = Query(None, description="Ex: '200-250' ou '220' ou '*'"),
    metragem_para_estimativa: Optional[float] = Query(None, description="Se enviado, usa diretamente como metragem alvo"),
    estado_conservacao: Optional[str] = Query("Padrão", description="reformado | original | Padrão"),

    tolerancia_m2_pct: float = Query(0.10, ge=0.0, le=0.5),
    tipo_negocio: str = Query("Venda")
):
    """
    Política de metragem alvo (idêntico ao script consultas_imoveis.py):
      1) Se 'metragem_para_estimativa' for enviada -> usa ela.
      2) Senão, se 'metragem' for intervalo -> usa Metragem do primeiro imóvel listado (mesmos filtros).
      3) Senão, se 'metragem' for número -> usa esse número.
      4) Senão -> None (pode inviabilizar cálculo do valor base).
    """
    # tempo de início (para medir tempo de processamento)
    start_time = time.time()

    # parse da metragem param (só interpretação, sem buscar ainda)
    pm = parse_metragem_param(metragem)

    # Conexão e cursor (usados também para obter o primeiro imóvel quando metragem é intervalo)
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao conectar no MySQL: {e}")

    try:
        # Se precisar listar resultados (para obter primeira metragem quando metragem é intervalo),
        # faz um SELECT com os mesmos filtros do script consultas_imoveis.py
        sql = "SELECT * FROM imoveis_df WHERE 1=1"
        params = []

        if isinstance(pm, tuple):
            sql += (" AND CAST(REPLACE(REPLACE(Metragem, ' m²', ''), ',', '.') "
                    "AS DECIMAL(10,2)) BETWEEN %s AND %s")
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
            sql, params = apply_like_tokens(sql, params, "endereco", endereco)
        if tipo_negocio:
            sql += " AND tipo_negocio LIKE %s"; params.append(f"%{tipo_negocio}%")

        sql += " ORDER BY CAST(REPLACE(REPLACE(VALOR, '.', ''), ',', '') AS UNSIGNED) DESC LIMIT %s"
        params.append(limite)

        cursor.execute(sql, params)
        resultados = cursor.fetchall()

        # Decide metragem_alvo seguindo a mesma ordem do script consultas_imoveis.py
        if isinstance(metragem_para_estimativa, (int, float)):
            metragem_alvo = float(metragem_para_estimativa)
            metragem_intervalo = None
        else:
            if isinstance(pm, tuple):
                metragem_intervalo = pm
                # usa a metragem do primeiro imóvel listado (se existir)
                if resultados:
                    metragem_alvo = parse_metragem_str_to_float(resultados[0].get("Metragem"))
                else:
                    metragem_alvo = None
            elif isinstance(pm, float):
                metragem_intervalo = None
                metragem_alvo = pm
            else:
                metragem_intervalo = None
                metragem_alvo = None

        # Cálculo do m² ponderado — usa 2000 comparáveis (igual ao script)
        valor_m2, n_usados, nivel, comps = media_m2_comparaveis(
            cursor,
            bairro=bairro, cidade=cidade, endereco=endereco,
            quartos=quartos, suites=suites, vagas=vagas, tipo=tipo,
            metragem_alvo=metragem_alvo,
            metragem_intervalo=(metragem_intervalo if isinstance(metragem_intervalo, tuple) else None),
            tipo_negocio=tipo_negocio,
            tolerancia_pct=tolerancia_m2_pct,
            trim_quantil=0.10,
            comparables_limit=2000,  # <-- alinhado ao script
        )
    finally:
        cursor.close()
        conn.close()

    if not valor_m2 or (metragem_alvo is None):
        elapsed = time.time() - start_time
        return {
            "ok": False,
            "mensagem": "Amostra insuficiente para calcular o valor do m² ou metragem alvo não definida.",
            "detalhes": {
                "metragem_alvo": metragem_alvo,
                "comparaveis_usados": n_usados,
                "nivel_base": nivel
            },
            "processado_em": f"{elapsed:.2f}s"
        }

    # Ajustes e estimativa
    valor_base = float(metragem_alvo) * float(valor_m2)

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

    # monta lista detalhada de comparáveis (para retorno JSON, para facilitar conferência com script)
    comparaveis_detalhados = [
        {"id": int(c[3]), "metragem": round(float(c[0]), 2), "valor": round(float(c[1]), 2), "valor_m2": round(float(c[2]), 2)}
        for c in comps
    ]

    return {
        "ok": True,
        "entrada": {
            "cidade": cidade, "bairro": bairro, "endereco": endereco, "tipo": tipo,
            "quartos": quartos, "suites": suites, "vagas": vagas,
            "metragem_param": metragem,
            "metragem_para_estimativa": metragem_para_estimativa,
            "estado_conservacao": estado_conservacao,
            "tolerancia_m2_pct": tolerancia_m2_pct,
            "tipo_negocio": tipo_negocio,
            "limite_listagem": limite
        },
        "resultado": {
            "metragem_alvo": round(metragem_alvo, 2),
            "valor_m2_ponderado": round(valor_m2, 2),
            "nivel_base": nivel,
            "comparaveis_usados": n_usados,
            "comparaveis_detalhados": comparaveis_detalhados,

            "valor_base": round(valor_base, 2),
            "ajuste_por_estado_pct": round(ajuste_pct, 4),
            "descricao_estado": desc_estado,

            "valor_estimado": round(valor_estimado, 2),
            "faixa_negociacao_min": round(faixa_min, 2),
            "faixa_negociacao_max": round(faixa_max, 2),

            "formatado_ptbr": {
                "valor_m2_ponderado": f"R$ {valor_m2:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "valor_base": fmt_brl(valor_base),
                "valor_estimado": fmt_brl(valor_estimado),
                "faixa_negociacao": f"{fmt_brl(faixa_min)} a {fmt_brl(faixa_max)}",
                "ajuste_por_estado": ("+" if ajuste_pct >= 0 else "") + f"{int(ajuste_pct*100)}%"
            }
        },
        "processado_em": f"{(time.time() - start_time):.2f}s"
    }

def _norm(s: str | None) -> str:
    return (s or "").strip()

def _upper_clean(s: str | None) -> str:
    # remove espaços duplicados e sobe para CAIXA ALTA
    return " ".join(_norm(s).split()).upper()

@app.get("/api/laudo/enderecos/{uf}")
def listar_enderecos_por_uf(
    uf: str = Path(..., description="UF ex: DF")
) -> Dict[str, Any]:
    t0 = time.perf_counter()

    uf_up = _upper_clean(uf)
    if not uf_up:
        raise HTTPException(status_code=400, detail="UF inválida.")

    conn = conectar()
    cur = conn.cursor(dictionary=True)
    try:
        sql = """
            SELECT cidade, bairro, endereco
            FROM endereco
            WHERE uf = %s
              AND cidade IS NOT NULL AND TRIM(cidade) <> ''
              AND bairro IS NOT NULL AND TRIM(bairro) <> ''
              AND endereco IS NOT NULL AND TRIM(endereco) <> ''
            ORDER BY cidade ASC, bairro ASC, endereco ASC
        """
        cur.execute(sql, (uf_up,))
        rows = cur.fetchall()

        mapa: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in rows:
            c = _upper_clean(row.get("cidade"))
            b = _upper_clean(row.get("bairro"))
            e = _upper_clean(row.get("endereco"))
            if c and b and e:
                mapa[c][b].add(e)

        saida: Dict[str, Any] = {}
        for cidade, bairros in mapa.items():
            saida[cidade] = {}
            for bairro, end_set in bairros.items():
                saida[cidade][bairro] = sorted(end_set)

        # tempo de processamento em segundos (ex: "0.12s")
        tempo_s = round((time.perf_counter() - t0), 2)
        saida["processado_em"] = f"{tempo_s}s"
        return saida
    finally:
        cur.close()
        conn.close()

@app.get("/api/laudo/tipos")
def listar_tipos() -> Dict[str, Any]:
    """
    Retorna todos os registros da tabela `tipo`.
    Saída: { "ok": True, "count": n, "tipos": [{ "id": id, "tipo": "..." }, ...], "processado_em": "0.12s" }
    """
    t0 = time.perf_counter()
    conn = conectar()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, tipo FROM tipo ORDER BY tipo ASC")
        rows = cur.fetchall()
        tipos = [{"id": int(r["id"]), "tipo": r["tipo"]} for r in rows]
        elapsed = round((time.perf_counter() - t0), 2)
        return {"ok": True, "count": len(tipos), "tipos": tipos, "processado_em": f"{elapsed}s"}
    finally:
        cur.close()
        conn.close()

# Execução:
# uvicorn api_laudo:app --reload --host 0.0.0.0 --port 8000