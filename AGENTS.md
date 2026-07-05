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
pip install -e .
black --check --diff .
ruff check .
flake8 .
pytest tests/ -v
```
