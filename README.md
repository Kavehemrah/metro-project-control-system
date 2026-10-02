# Metro Project Control System

نسخه اولیه سیستم کنترل پروژه مترو بر پایه Python + PySide6 + SQLite.

## Version 0.1
- RTL management dashboard
- SQLite data layer
- Project KPI dashboard
- Initial Excel import module
- Seed data based on the Baharestan metro operational plan

## Modules
- Operational plan: create, edit, and remove activities.
- Physical progress: update progress percentage, delay, and status for an activity.
- Finance: view monthly revenue, cost, and balances.
- Resources, workforce, and materials: manage required and available amounts.
- Risks: manage probability, impact, controls, corrective actions, and calculated score.
- Reports: export activity, resource, and risk tables to Excel-compatible CSV files.
- Settings: import project name, reporting period, and financial KPIs from an Excel workbook.

The current Excel importer does not import activity, resource, or risk rows. Those
records are managed separately in their respective modules. Initial activity,
resource, and risk entries are starter data and should be verified before use.

Import an Excel workbook from the project root with:
```bash
python -m imports.import_excel "metro-project-control-system.xlsx"
```

## Run
```bash
python -m pip install -r requirements.txt
python main.py
```
