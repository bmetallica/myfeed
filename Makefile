.PHONY: up build rebuild logs down reset ps

# Alle Container starten (ohne Rebuild)
up:
	docker compose up -d

# Geänderte Images bauen und Container neu starten
build:
	docker compose build
	docker compose up -d

# Vollständiger Rebuild ohne Cache (z.B. nach Dependency-Änderungen)
rebuild:
	docker compose build --no-cache
	docker compose up -d

# Live-Logs aller Container (Ctrl+C zum Beenden)
logs:
	docker compose logs -f

# Alle Container stoppen
down:
	docker compose down

# Alle Container + Volumes löschen (Achtung: löscht Datenbankdaten!)
reset:
	docker compose down -v

# Container-Status anzeigen
ps:
	docker compose ps
