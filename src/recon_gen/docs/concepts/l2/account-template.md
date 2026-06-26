# Account template

An account template declares the SHAPE of a 1-of-many account class — say,
every customer demand-deposit account, or every per-product subledger. The L2
YAML declares the template ONCE; the physical account instances get selected at
posting time, usually off ``Transaction.Metadata`` (``customer_id``,
``merchant_id``).

It carries a ``role`` and a ``scope``, plus an optional ``parent_role``. That
``parent_role`` MUST resolve to a singleton [Account](account.md) — never
another template. The validator rejects a template that points at another
template at load time (the SPEC's "singleton parent only" rule).

Every materialized instance rolls up to the template's parent singleton — so
every customer DDA aggregates into the ``DDAControl`` account on the GL, and the
L1 drift checks compute parent rollups against that control number.

``instance_id_template`` + ``instance_name_template`` let the demo seed
synthesize per-instance ids and display names with your own pattern (defaults:
``cust-{n:03d}`` / ``Customer {n}``). Both accept ``{role}`` and ``{n}`` as
placeholders.

> Templates are why the L1 dashboard's drift sheet shows two views:
> the per-leaf drill (every individual customer DDA) AND the parent
> rollup (the sum across the control account). The parent rollup is
> what catches a "1-cent off across all 50,000 customers" drift that
> no single leaf would surface.

## Specific example for you

{{ l2_account_template_focus() }}
