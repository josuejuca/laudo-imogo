from collections import defaultdict
from typing import Dict, Set, Any
from fastapi import FastAPI, Depends, Path, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv
import os, time
from fastapi.middleware.cors import CORSMiddleware
# =========================
# Configuração
# =========================
load_dotenv()

# MYSQL_URL = "mysql+pymysql://root:@localhost/dfdb?charset=utf8mb4"
MYSQL_URL = "mysql+pymysql://root:@localhost/dfdb?charset=utf8mb4"

engine = create_engine(MYSQL_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

app = FastAPI(title="imoGo — Endereços", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _norm(s: str | None) -> str:
    return (s or "").strip()

def _upper_clean(s: str | None) -> str:
    # remove espaços duplicados e sobe para CAIXA ALTA
    return " ".join(_norm(s).split()).upper()

# =========================
# GET /api/laudo/enderecos/{uf}
# =========================
@app.get("/api/laudo/enderecos/{uf}")
def listar_enderecos_por_uf(
    uf: str = Path(..., description="UF ex: DF"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    t0 = time.perf_counter()

    uf_up = _upper_clean(uf)
    if not uf_up:
        raise HTTPException(status_code=400, detail="UF inválida.")

    # Busca tudo da tabela endereco para a UF informada
    # Colunas: uf, cidade, bairro, endereco
    sql = text("""
        SELECT cidade, bairro, endereco
        FROM endereco
        WHERE uf = :uf
          AND cidade IS NOT NULL AND TRIM(cidade) <> ''
          AND bairro IS NOT NULL AND TRIM(bairro) <> ''
          AND endereco IS NOT NULL AND TRIM(endereco) <> ''
        ORDER BY cidade ASC, bairro ASC, endereco ASC
    """)
    rows = db.execute(sql, {"uf": uf_up}).all()

    # Mapa -> CIDADE: { BAIRRO: set(ENDERECO) }
    mapa: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))

    for cidade, bairro, endereco in rows:
        c = _upper_clean(cidade)
        b = _upper_clean(bairro)
        e = _upper_clean(endereco)
        if c and b and e:
            mapa[c][b].add(e)

    # Converte sets para listas ordenadas
    saida: Dict[str, Any] = {}
    for cidade, bairros in mapa.items():
        saida[cidade] = {}
        for bairro, end_set in bairros.items():
            saida[cidade][bairro] = sorted(end_set)

    # tempo de processamento
    tempo_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    # pedido: "coloca no final o tempo de processamento"
    # JSON não garante ordem, mas adicionamos como última chave construída.
    saida["tempo_ms"] = tempo_ms
    return saida
