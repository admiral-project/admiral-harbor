# admiral-harbor

`admiral-harbor` es el portal de cliente de Admiral.

Hace:

- autentica clientes finales.
- muestra estado de apps e instancias del cliente.
- gestiona suscripciones y pagos.
- permite operaciones sobre instancias del cliente.

No hace:

- administrar infraestructura.
- conocer nodos ni workloads directamente.
- duplicar lógica de negocio de `admirald`.

Reglas:

- portal de cliente, no de operador.
- sin acceso a admin endpoints de `admirald`.
- sin lógica de infraestructura.

## Pre-commit

Ejecutar estos comandos antes de cada commit:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
black --check --diff app/ tests/ dev_run.py run.py worker.py cli.py
ruff check app/ tests/ dev_run.py run.py worker.py cli.py
flake8 app/ tests/ dev_run.py run.py worker.py cli.py
pytest tests/ -v
```
