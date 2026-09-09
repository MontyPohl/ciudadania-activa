---
name: refactoring-ui-design
description: Use when designing, redesigning, polishing, or reviewing user interfaces, web apps, dashboards, landing pages, forms, tables, empty states, visual hierarchy, spacing, typography, color systems, shadows, images, icons, or frontend CSS with the practical heuristics from Adam Wathan and Steve Schoger's Refactoring UI. Trigger for requests like "make this UI look better", "apply Refactoring UI", "polish this page", "review the design", "improve hierarchy/spacing/colors/typography", or "build a UI that follows Refactoring UI".
---

# Refactoring UI Design

Apply Refactoring UI as an execution checklist, not as decoration. Start from product function and hierarchy, constrain choices with systems, then polish typography, color, depth, imagery, and details.

## Core Workflow

1. Identify the actual feature or user task before touching layout chrome. Design the smallest useful version that can ship.
2. Work in grayscale first when the hierarchy is unclear. Use size, weight, contrast, and spacing before relying on color.
3. Establish or reuse systems for type, spacing, sizing, color, radii, border widths, opacity, and shadows. Avoid arbitrary one-off values.
4. Make hierarchy explicit: primary content and actions should stand out; secondary and tertiary content should be de-emphasized without becoming illegible.
5. Shape layout with deliberate whitespace and group spacing. Use fixed or max widths when content has an intended width; use grids only where fluid sizing is actually useful.
6. Tune text, color, depth, imagery, and finishing details after the structure works.
7. Verify the result in the actual rendered surface, including desktop and mobile states when applicable. Check that text fits, contrast holds, grouping is clear, and no visual element competes with the primary task.

## Load The Reference

Read [references/refactoring-ui-principles.md](references/refactoring-ui-principles.md) when doing any substantive UI creation, redesign, review, or CSS implementation. It contains the condensed rule set by topic and the final review checklist.

## Implementation Defaults

- Prefer a limited, named scale over ad hoc CSS values.
- Prefer `rem` or project tokens for type scale values; avoid nested `em` font sizing that falls off the scale.
- Prefer system fonts or proven neutral UI fonts unless the product personality calls for a specific typeface.
- Prefer 2-3 text colors for hierarchy in a local area: strong primary, softer secondary, subtle tertiary.
- Prefer fewer borders. Try spacing, background shifts, or shadows first.
- Prefer icons at their intended size; put small icons inside a larger colored shape instead of scaling them up.
- Prefer real content and realistic states, including empty states and user-uploaded content edge cases.

## Review Output

When reviewing a UI, lead with the highest-impact issues first. For each issue, state:

- what feels wrong or risky,
- the Refactoring UI principle involved,
- the concrete change to make,
- any responsive or accessibility check required.

Keep suggestions specific enough to implement directly in code.
