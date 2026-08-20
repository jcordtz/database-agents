# db-agents

![Tiger header](images/tiger.jpg)

[![English](images/lang-en-red.svg)](README.md) [![Dansk](images/lang-da--dk-green.svg)](README.da.md) [![Deutsch](images/lang-de-yellow.svg)](README.de.md)

> **Haftungsausschluss:** Dieses Projekt wird **wie besehen** ("as-is")
> bereitgestellt, ohne Gewährleistungen oder Bedingungen jeglicher Art, und
> ist zur Nutzung unter der **MIT-Lizenz** vorgesehen.

Verwandle jede Tabelle in deinen MSSQL-, Oracle-, PostgreSQL- und DB2-
Datenbanken in einen selbstbeschreibenden "Agenten" und stelle alle über einen
einzigen **MCP**-Server bereit, sodass Endanwender natürliche Fragen über
mehrere Tabellen (und mehrere Datenbanken) hinweg stellen können.

## Funktionsweise

```
                      ┌─────────────────────────────┐
 config.yaml  ──────► │  DatabaseConnectionConfig[]  │
                      └──────────────┬───────────────┘
                                     │ SQLAlchemy engine per connection
                                     ▼
                      ┌─────────────────────────────┐
                      │   Metadata Introspector      │  reflection (columns,
                      │   (db_agents/metadata)        │  types, PK/FK) +
                      └──────────────┬───────────────┘  dialect comment SQL
                                     ▼
                      ┌─────────────────────────────┐
                      │  Description Generator       │  structured summary,
                      │  (db_agents/agents/description)│ optionally rewritten
                      └──────────────┬───────────────┘  by the LLM
                                     ▼
                      ┌─────────────────────────────┐
                      │  TableAgent + AgentRegistry   │  one agent per table,
                      │  (cached in SQLite)           │  catalog of all agents
                      └──────────────┬───────────────┘
                                     ▼
                      ┌─────────────────────────────┐
                      │      Orchestrator             │  1) select tables
                      │  (db_agents/orchestrator.py)  │  2) generate SQL
                      │                                │  3) execute (read-only)
                      │                                │  4) synthesize answer
                      └──────────────┬───────────────┘
                                     ▼
                      ┌─────────────────────────────┐
                      │       MCP Server              │  list_tables,
                      │  (db_agents/mcp_server)        │  describe_table,
                      │                                │  refresh_metadata,
                      │                                │  ask_question
                      └─────────────────────────────┘
```

1. **Metadaten-Introspektion** (`db_agents/metadata`) nutzt SQLAlchemy `Inspector`
   für strukturierte Reflection (Spalten, Datentypen, Primär-/Fremdschlüssel).
   Tabellen-/Spalten-*Kommentare* sind in SQLAlchemy nicht einheitlich verfügbar,
   daher hat jeder Dialekt eine kleine explizite SQL-Abfrage
   (`comment_fetchers.py`):
   - PostgreSQL: `obj_description` / `col_description`
   - MSSQL: `sys.extended_properties` (`MS_Description`)
   - Oracle: `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS`
   - DB2: `SYSCAT.TABLES` / `SYSCAT.COLUMNS` (`REMARKS`)

2. **Purview-Anreicherung** (`db_agents/purview` +
   `db_agents/agents/purview_enrichment.py`, optional). Falls konfiguriert,
   wird jede introspektierte Tabelle/Spalte über die Microsoft-Purview-Data-Map
   (Atlas) API anhand eines **qualified name** gesucht, der aus
   Host/Database/Schema/Table der Verbindung erzeugt wird
   (`db_agents/purview/qualified_name.py`).

3. **Beschreibungsgenerierung** (`db_agents/agents/description.py`) erstellt
   immer zuerst eine deterministische, strukturierte Zusammenfassung aus Fakten
   (Kommentare, Spalten + Datentypen, PK/FK, Referenzen, Purview-Kontext). Ist
   ein LLM konfiguriert, wird diese Zusammenfassung sprachlich überarbeitet.
   Das LLM darf nur umformulieren, keine Fakten erfinden.

4. **Agent pro Tabelle** (`db_agents/agents/table_agent.py`) kapselt Metadaten +
   Beschreibung einer Tabelle und stellt einen kompakten Katalogeintrag bereit.

5. **AgentRegistry** (`db_agents/agents/registry.py`) entdeckt alle Tabellen
   über alle Verbindungen, führt optional Purview-Lookups aus, erstellt Agents
   und cached Beschreibungen lokal in SQLite.

6. **Orchestrator** (`db_agents/orchestrator.py`) beantwortet Fragen über mehrere
   Tabellen in vier Schritten:
   - LLM wählt relevante Tabellen aus dem Katalog.
   - LLM erzeugt pro Datenbankverbindung eine read-only `SELECT`-Abfrage.
   - SQL wird validiert (`SELECT`/`WITH` erlaubt, schreibende Befehle verboten).
   - Ergebnisse werden ausgeführt und vom LLM zur Endantwort zusammengeführt.

7. **MCP-Server** (`db_agents/mcp_server`) stellt vier Tools bereit:
   - `list_tables` — alle gefundenen Tabellen mit Kurzbeschreibung
   - `describe_table(table_id)` — vollständige Beschreibung + Schema (+ Purview)
   - `refresh_metadata(force=False)` — Metadaten neu einlesen + neu beschreiben
   - `ask_question(question, row_limit=1000)` — zentraler Endnutzer-Einstieg

## Einrichtung

Es gibt zwei Wege zu einer lauffähigen Einrichtung:

- **[Aus CSV generieren](#setup-aus-csv-generieren-empfohlen)** —
  empfohlen, wenn du bereits eine Tabellenliste hast.
- **[Manuelle Konfiguration](#manuelles-setup)** — für kleine/einmalige Setups.

## Setup aus CSV generieren (empfohlen)

`scripts/create-agent-setup.sh` erstellt ein vollständiges Setup
(`config.yaml`, `.env`-Vorlage, Launcher und optional `.venv`) aus zwei Inputs.

### 1. Tabellen-CSV

Eine Zeile pro Tabelle, die einen Agenten erhalten soll:

```csv
db_type,host,schema,table
postgresql,pg.example.com,public,orders
postgresql,pg.example.com,public,customers
mssql,crm-db.example.com,dbo,Customers
oracle,fin-db.example.com,FINANCE,GL_ACCOUNTS
db2,legacy-db.example.com,LEGACY,ORDERS
```

- Spaltennamen sind case-insensitive, gängige Aliase werden unterstützt.
- `db_type` akzeptiert z. B. `SQL Server`, `Postgres`, `IBM DB2`.
- Leere Zeilen und `#`-Kommentare werden ignoriert; Duplikate entfernt.
- Optionale Spalte `database` erlaubt mehrere Datenbanken auf einem Host.

Siehe [`examples/tables.example.csv`](examples/tables.example.csv).

### 2. Verbindungsordner

Eine `<host>.properties`-Datei je Host in der CSV:

```properties
# pg.example.com.properties
port=5432
database=sales
username=sales_reader
password_env=PG_SALES_PASSWORD

# optional: any "driver.<name>" entry becomes a SQLAlchemy driver option
driver.sslmode=require

# optional Purview overrides for this source
purview_source_host=pg.example.com
purview_database=sales
```

Unterstützte Schlüssel: `port`, `database`, `username`, `password_env`, `host`,
`sqlalchemy_url`, `driver.*` und `purview_*`. **Keine Passwörter in diesen
Dateien** — `password_env` verweist nur auf den Namen einer Umgebungsvariable.

Siehe [`examples/connections/`](examples/connections).

### 3. Script ausführen

```bash
./scripts/create-agent-setup.sh
```

Fehlende Angaben werden abgefragt (inkl. **Zielordner für das Setup**).
Für Automation alles direkt per Optionen angeben:

```bash
./scripts/create-agent-setup.sh \
    --tables-csv examples/tables.example.csv \
    --connections-dir examples/connections \
    --target-dir ~/db-agents-prod \
    --llm-deployment gpt-4o \
    --no-purview \
    --non-interactive
```

Oder Werte in einer Optionsdatei hinterlegen (`--options-file`):

```properties
tables_csv=examples/tables.example.csv
connections_dir=examples/connections
target_dir=/opt/db-agents
llm_deployment=gpt-4o
```

Nützliche Flags: `--purview-endpoint URL`, `--force`, `--skip-venv`,
`--python PATH`, `--non-interactive`.

### Ergebnis

```
<target-dir>/
├── config.yaml          # Verbindungen, Schemas und exakte Tabellenliste
├── .env                 # Vorlage mit allen benötigten Variablen
├── run-mcp-server.sh    # lädt .env und startet den MCP-Server
├── README.md            # setup-spezifische Hinweise
├── inputs/              # Kopie von CSV + properties zur Regeneration
└── .venv/               # db_agents + nur die benötigten DB-Treiber
```

Danach `<target-dir>/.env` ausfüllen und starten:

```bash
<target-dir>/run-mcp-server.sh
```

Da `config.yaml` `include_tables` aus der CSV setzt, werden nur die gelisteten
Tabellen introspektiert und als Agenten erzeugt.

## Manuelles Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# install the driver(s) for the databases you actually need, e.g.:
pip install -e ".[postgresql]"      # PostgreSQL only
pip install -e ".[all-db]"          # every dialect
```

Beispiel-Konfiguration und env-Datei kopieren:

```bash
cp examples/config.example.yaml config.yaml
cp .env.example .env
```

`config.yaml` mit echten Verbindungsdaten befüllen (host/port/database/username/schemas).
**Passwörter niemals direkt in YAML speichern**; stattdessen `password_env` nutzen.

## Ausführung

`.env` laden und MCP-Server starten:

```bash
set -a; source .env; set +a
db-agents-mcp
# or: python -m db_agents.mcp_server.server
```

## Microsoft Purview aktivieren (optional)

1. Service Principal in Azure AD erstellen/wiederverwenden (Data Reader-Rechte).
2. `purview.enabled: true` und `purview.account_endpoint` in `config.yaml` setzen.
3. `PURVIEW_TENANT_ID`, `PURVIEW_CLIENT_ID`, `PURVIEW_CLIENT_SECRET` in `.env`.
4. Qualified names werden automatisch pro Tabelle/Spalte erzeugt.
5. Server neu starten (oder `refresh_metadata` ausführen).

## Neue Datenbank hinzufügen

1. Neuen Eintrag in `databases:` mit Dialekt `mssql|oracle|postgresql|db2`.
2. Passendes Extra installieren: `pip install -e ".[<dialect>]"`.
3. Server neu starten (oder `refresh_metadata`).

Bei CSV-basiertem Setup: neue Zeilen + passende `<host>.properties` ergänzen
und Script mit `--force` erneut ausführen.

## Tests

```bash
pytest -q
```

## Sicherheitshinweise

- Generiertes SQL ist auf read-only `SELECT`/`WITH` begrenzt.
- Verwende read-only DB-Rollen; der SQL-Filter ist nur ein Sicherheitsnetz.
- Passwörter und Purview-Secret werden nur aus Umgebungsvariablen gelesen.
- Der Purview-Service-Principal sollte nur read-only Berechtigungen haben.

---

[![English](images/lang-en-red.svg)](README.md) [![Dansk](images/lang-da--dk-green.svg)](README.da.md) [![Deutsch](images/lang-de-yellow.svg)](README.de.md)
