-- Zhejiang University Journal (Agriculture & Life Sciences) relational schema
CREATE DATABASE IF NOT EXISTS journal_kg
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE journal_kg;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS paper_awards;
DROP TABLE IF EXISTS paper_clc;
DROP TABLE IF EXISTS paper_keywords;
DROP TABLE IF EXISTS author_institutions;
DROP TABLE IF EXISTS paper_authors;
DROP TABLE IF EXISTS funds;
DROP TABLE IF EXISTS keywords;
DROP TABLE IF EXISTS institutions;
DROP TABLE IF EXISTS authors;
DROP TABLE IF EXISTS papers;

CREATE TABLE papers (
  doi VARCHAR(128) NOT NULL PRIMARY KEY,
  source_xml VARCHAR(255) NULL,
  publisher_article_id VARCHAR(255) NULL,
  title_zh TEXT NULL,
  title_en TEXT NULL,
  abstract_zh MEDIUMTEXT NULL,
  abstract_en MEDIUMTEXT NULL,
  pub_date VARCHAR(32) NULL,
  received_date VARCHAR(32) NULL,
  year SMALLINT NULL,
  volume VARCHAR(32) NULL,
  issue VARCHAR(32) NULL,
  fpage VARCHAR(32) NULL,
  lpage VARCHAR(32) NULL,
  pdf VARCHAR(255) NULL,
  document_type VARCHAR(32) NULL,
  issn VARCHAR(32) NULL,
  journal_id VARCHAR(64) NULL,
  journal_title_zh VARCHAR(255) NULL,
  author_count INT NULL DEFAULT 0,
  keyword_count INT NULL DEFAULT 0,
  has_funding TINYINT(1) NOT NULL DEFAULT 0,
  KEY idx_papers_year (year),
  KEY idx_papers_journal (journal_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE authors (
  author_id VARCHAR(64) NOT NULL PRIMARY KEY,
  name_zh VARCHAR(255) NULL,
  name_en VARCHAR(255) NULL,
  email VARCHAR(255) NULL,
  emails TEXT NULL,
  paper_count INT NULL DEFAULT 0,
  KEY idx_authors_name_zh (name_zh)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE institutions (
  institution_id VARCHAR(64) NOT NULL PRIMARY KEY,
  name_norm VARCHAR(512) NULL,
  full_norm VARCHAR(512) NULL,
  name_zh TEXT NULL,
  name_en TEXT NULL,
  postcode VARCHAR(32) NULL,
  paper_count INT NULL DEFAULT 0,
  KEY idx_institutions_name_norm (name_norm(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE keywords (
  keyword_id VARCHAR(64) NOT NULL PRIMARY KEY,
  label_zh VARCHAR(255) NULL,
  label_en VARCHAR(255) NULL,
  paper_count INT NULL DEFAULT 0,
  KEY idx_keywords_label_zh (label_zh)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE funds (
  fund_id VARCHAR(64) NOT NULL PRIMARY KEY,
  agency_norm VARCHAR(255) NULL,
  program_type VARCHAR(255) NULL,
  example_raw TEXT NULL,
  paper_count INT NULL DEFAULT 0,
  KEY idx_funds_agency (agency_norm)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE paper_authors (
  doi VARCHAR(128) NOT NULL,
  author_id VARCHAR(64) NOT NULL,
  author_order INT NOT NULL DEFAULT 0,
  name_zh VARCHAR(255) NULL,
  name_en VARCHAR(255) NULL,
  is_corresponding TINYINT(1) NOT NULL DEFAULT 0,
  institution_ids TEXT NULL,
  aff_xml_ids VARCHAR(255) NULL,
  -- author_id 清洗中存在少量碰撞，作者位次用 (doi, author_order) 唯一标识
  PRIMARY KEY (doi, author_order),
  KEY idx_pa_author (author_id),
  KEY idx_pa_doi_author (doi, author_id),
  CONSTRAINT fk_pa_paper FOREIGN KEY (doi) REFERENCES papers(doi),
  CONSTRAINT fk_pa_author FOREIGN KEY (author_id) REFERENCES authors(author_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE author_institutions (
  doi VARCHAR(128) NOT NULL,
  author_id VARCHAR(64) NOT NULL,
  institution_id VARCHAR(64) NOT NULL,
  author_order INT NULL DEFAULT 0,
  PRIMARY KEY (doi, author_id, institution_id),
  KEY idx_ai_author (author_id),
  KEY idx_ai_institution (institution_id),
  CONSTRAINT fk_ai_paper FOREIGN KEY (doi) REFERENCES papers(doi),
  CONSTRAINT fk_ai_author FOREIGN KEY (author_id) REFERENCES authors(author_id),
  CONSTRAINT fk_ai_institution FOREIGN KEY (institution_id) REFERENCES institutions(institution_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE paper_keywords (
  doi VARCHAR(128) NOT NULL,
  keyword_id VARCHAR(64) NOT NULL,
  label_zh VARCHAR(255) NULL,
  label_en VARCHAR(255) NULL,
  PRIMARY KEY (doi, keyword_id),
  KEY idx_pk_keyword (keyword_id),
  CONSTRAINT fk_pk_paper FOREIGN KEY (doi) REFERENCES papers(doi),
  CONSTRAINT fk_pk_keyword FOREIGN KEY (keyword_id) REFERENCES keywords(keyword_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE paper_clc (
  doi VARCHAR(128) NOT NULL,
  clc_code VARCHAR(64) NOT NULL,
  clc_parent VARCHAR(64) NULL,
  PRIMARY KEY (doi, clc_code),
  KEY idx_clc_code (clc_code),
  CONSTRAINT fk_clc_paper FOREIGN KEY (doi) REFERENCES papers(doi)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE paper_awards (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  doi VARCHAR(128) NOT NULL,
  fund_id VARCHAR(64) NOT NULL,
  agency_norm VARCHAR(255) NULL,
  program_type VARCHAR(255) NULL,
  funding_raw TEXT NULL,
  award_id VARCHAR(128) NULL,
  KEY idx_award_doi (doi),
  KEY idx_award_fund (fund_id),
  CONSTRAINT fk_award_paper FOREIGN KEY (doi) REFERENCES papers(doi),
  CONSTRAINT fk_award_fund FOREIGN KEY (fund_id) REFERENCES funds(fund_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
