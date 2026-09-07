# urbantransit
GTFS parsing to parquet format and tools to use it

Projet Python gere par Pixi.

## Prerequis

- [Pixi](https://pixi.sh/latest/)

## Installation

```bash
pixi install
```

## Execution

Afficher l'aide :

```bash
pixi run run
```

Valider un flux GTFS ou un dossier de flux :

```bash
pixi run validate -- ./data/gtfs
```

Convertir les flux GTFS d'un dossier en Parquet :

```bash
pixi run parse -- ./data/gtfs ./data/parquet --crs EPSG:2154 --year 2026 --week 10
```

Nettoyer les flux GTFS d'un dossier :

```bash
pixi run clean -- ./data/gtfs
```

Les mêmes commandes sont disponibles directement après installation avec
`urbantransit validate`, `urbantransit parse` et `urbantransit clean`.

## Structure

- `src/urbantransit/` : package Python
- `pyproject.toml` : environnement, tâches Pixi et métadonnées du package
