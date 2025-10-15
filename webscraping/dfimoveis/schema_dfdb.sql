-- Cria o banco (ajuste se já existir)
CREATE DATABASE IF NOT EXISTS dfdb
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;

USE dfdb;

-- Tabela principal
CREATE TABLE IF NOT EXISTS imoveis_df (
  ID BIGINT(20) NOT NULL,
  CIDADE VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  BAIRRO VARCHAR(160) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  endereco VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  tipo VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  Titulo VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  Metragem VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  QUARTOS TINYINT(4) NULL,
  SUITES TINYINT(4) NULL,
  VAGAS TINYINT(4) NULL,
  VALOR VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  tipo_negocio VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  valor_m2 VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  data_da_busca VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  PRIMARY KEY (ID),
  KEY idx_cidade (CIDADE),
  KEY idx_bairro (BAIRRO)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
