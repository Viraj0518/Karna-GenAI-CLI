# Recipes

Recipes are declarative YAML specifications for agent runs. They define
instructions, parameters, tool allowlists, and optional sub-recipe
invocations. Recipes live as `.yaml` files under `~/.karna/recipes/` or
inline in a project directory.

## Recipe YAML Format

```yaml
name: triage_ticket
description: Summarise an inbound ticket and recommend triage
parameters:
  - name: ticket_id
    type: string
    required: true
  - name: priority
    type: string
    default: normal
extensions:
  - name: db
  - name: web_fetch
instructions: |
  You are triaging ticket {{ ticket_id }} at {{ priority }} priority.
  Read the ticket from the database. Summarise in 3 bullets.
model: openrouter:anthropic/claude-haiku-4.5
max_iterations: 20
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier for the recipe |
| `instructions` | Yes | Jinja2-templated prompt sent to the agent |
| `description` | No | Human-readable summary |
| `parameters` | No | List of typed inputs the recipe accepts |
| `extensions` | No | Tool allowlist (empty = all tools allowed) |
| `model` | No | Model override (e.g. `openrouter:anthropic/claude-haiku-4.5`) |
| `max_iterations` | No | Agent loop iteration cap (default: 25) |
| `sub_recipes` | No | List of sub-recipe invocations |
| `schedule` | No | Cron expression for scheduled execution |

### Parameter Types

Parameters support four types with automatic coercion:

- `string` (default)
- `integer`
- `number` (float)
- `boolean` (accepts: true/false/yes/no/1/0/on/off)

## Sub-Recipe Declaration

Sub-recipes allow a parent recipe to delegate sub-tasks to child recipes
that run as independent subagents. Each sub-recipe has its own
conversation context, instructions, and parameter set.

### Syntax

```yaml
name: research_report
description: Generate a research report with analysis
parameters:
  - name: topic
    type: string
    required: true
  - name: depth
    type: string
    default: standard
sub_recipes:
  - name: research
    recipe: research.yaml
    inputs:
      query: "{{ topic }}"
      max_sources: "5"
    description: Gather research materials
  - name: analysis
    recipe: analysis.yaml
    inputs:
      data: "{{ sub.research }}"
      depth: "{{ depth }}"
    description: Analyse gathered research
instructions: |
  Create a report on {{ topic }}.

  Research findings:
  {{ sub.research }}

  Analysis:
  {{ sub.analysis }}

  Synthesise these into a final report.
```

### Sub-Recipe Entry Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Identifier used to reference output as `{{ sub.<name> }}` |
| `recipe` | Yes | Path to the sub-recipe YAML (relative to parent recipe dir) |
| `inputs` | No | Mapping of parameter values (supports Jinja2 templates) |
| `description` | No | Human-readable description for logging |

## Parameter Flow

### Parent to Sub-Recipe

Input values declared in `inputs` are rendered as Jinja2 templates
against the parent recipe's resolved parameter context:

```yaml
# Parent recipe has parameter: topic = "vaccines"
sub_recipes:
  - name: fetch
    recipe: fetch.yaml
    inputs:
      query: "{{ topic }}"  # Resolves to "vaccines"
      limit: "10"           # Static value
```

### Sub-Recipe to Parent

Sub-recipe outputs are injected into the parent's template context under
the `sub` namespace. Access them as `{{ sub.<name> }}`:

```yaml
instructions: |
  The research returned: {{ sub.fetch }}
  Now synthesise a final answer.
```

### Chaining Sub-Recipes

Later sub-recipes can reference earlier sub-recipe outputs:

```yaml
sub_recipes:
  - name: step1
    recipe: gather.yaml
    inputs:
      topic: "{{ query }}"
  - name: step2
    recipe: analyse.yaml
    inputs:
      data: "{{ sub.step1 }}"  # Uses output from step1
```

Note: Sub-recipes execute sequentially in declaration order, so `step2`
can reference `{{ sub.step1 }}` because `step1` has already completed.

## Nesting Limits

Sub-recipes can invoke their own sub-recipes, enabling hierarchical
task decomposition. The maximum nesting depth is **3 levels**:

```
Level 1: Parent Recipe
  Level 2: Sub-Recipe (invoked by parent)
    Level 3: Sub-Sub-Recipe (invoked by level 2)
      Level 4: BLOCKED - MaxDepthExceededError
```

Exceeding 3 levels raises `MaxDepthExceededError`. This prevents
runaway recursion while allowing meaningful decomposition.

## Execution Model

1. Parent recipe parameters are resolved and validated
2. Sub-recipes execute sequentially in declaration order
3. Each sub-recipe spawns an independent subagent with its own conversation
4. Sub-recipe outputs are collected into `{{ sub.<name> }}`
5. Parent instructions are rendered with the full context (params + sub results)
6. Parent agent loop executes with the rendered instructions

## Examples

### Simple Delegation

```yaml
# parent.yaml
name: code_review
parameters:
  - name: pr_url
    type: string
    required: true
sub_recipes:
  - name: diff
    recipe: fetch_diff.yaml
    inputs:
      url: "{{ pr_url }}"
instructions: |
  Review this PR diff and provide feedback:
  {{ sub.diff }}
```

```yaml
# fetch_diff.yaml
name: fetch_diff
parameters:
  - name: url
    type: string
    required: true
instructions: |
  Fetch the diff from {{ url }} and return it formatted.
extensions:
  - web_fetch
```

### Multi-Step Pipeline

```yaml
name: incident_response
parameters:
  - name: alert_id
    type: string
    required: true
sub_recipes:
  - name: triage
    recipe: triage.yaml
    inputs:
      alert: "{{ alert_id }}"
  - name: investigate
    recipe: investigate.yaml
    inputs:
      alert: "{{ alert_id }}"
      triage_summary: "{{ sub.triage }}"
  - name: remediate
    recipe: remediate.yaml
    inputs:
      findings: "{{ sub.investigate }}"
instructions: |
  Incident response complete for alert {{ alert_id }}.

  Triage: {{ sub.triage }}
  Investigation: {{ sub.investigate }}
  Remediation: {{ sub.remediate }}

  Summarise the incident and next steps.
```

### Nested Sub-Recipes (3 Levels)

```yaml
# level1.yaml
name: deep_research
sub_recipes:
  - name: analysis
    recipe: level2.yaml
    inputs:
      topic: "{{ query }}"
instructions: |
  Final synthesis based on: {{ sub.analysis }}
```

```yaml
# level2.yaml
name: analysis
parameters:
  - name: topic
    type: string
    required: true
sub_recipes:
  - name: data
    recipe: level3.yaml
    inputs:
      search: "{{ topic }}"
instructions: |
  Analyse data: {{ sub.data }}
```

```yaml
# level3.yaml (deepest allowed level)
name: data_fetch
parameters:
  - name: search
    type: string
    required: true
instructions: |
  Fetch raw data for: {{ search }}
extensions:
  - web_fetch
  - db
```

## CLI Usage

```bash
# Run a recipe
nellie run --recipe triage_ticket.yaml --param ticket_id=CDC-4021

# Run with multiple parameters
nellie run --recipe research.yaml --param topic=vaccines --param depth=deep
```

## Error Handling

| Error | Cause |
|-------|-------|
| `SubRecipeNotFoundError` | Referenced YAML file does not exist |
| `SubRecipeParameterError` | Parameter validation or Jinja2 rendering failed |
| `MaxDepthExceededError` | Nesting exceeds 3 levels |
| `RecipeLoadError` | Malformed YAML or unknown fields in sub-recipe |

## API

```python
from karna.recipes import run_recipe, run_sub_recipe, run_all_sub_recipes

# Full recipe execution (handles sub-recipes automatically)
result = await run_recipe(recipe, params, recipe_path="path/to/recipe.yaml")

# Manual sub-recipe invocation
output = await run_sub_recipe(
    parent_recipe=recipe,
    sub_ref=recipe.sub_recipes[0],
    parent_context=resolved_params,
    provider=provider,
    tools=tools,
    parent_recipe_path=Path("path/to/parent.yaml"),
)

# Run all sub-recipes and get results dict
results = await run_all_sub_recipes(
    recipe, resolved_params, provider, tools,
    recipe_path=Path("path/to/recipe.yaml"),
)
# results == {"research": "...", "analysis": "..."}
```
