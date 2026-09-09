# Refactoring UI Principles

Use this reference when creating, polishing, or reviewing UI. It is a condensed, original execution guide based on the principles in *Refactoring UI* by Adam Wathan and Steve Schoger.

## Contents

- Start From The Feature
- Systems Before Tweaks
- Hierarchy
- Layout And Spacing
- Text
- Color
- Depth
- Images And Icons
- Finishing Details
- Final Review Checklist

## Start From The Feature

- Start with real functionality, not the app shell. Build the central user task before nav, sidebars, logos, or page chrome.
- Delay detail. Explore layout and hierarchy before typefaces, shadows, icons, gradients, or exact colors.
- Begin in grayscale when the structure is weak. If the design only works after color is added, the hierarchy is probably not strong enough.
- Work in short cycles. Design the simple version, implement it, then let real edge cases guide the next pass.
- Avoid implying unbuilt functionality. Remove nice-to-have controls until they are actually part of the shippable scope.
- Choose a personality deliberately: serious, playful, luxurious, technical, calm, editorial, operational, etc. Let font choice, palette, radius, language, and imagery support the same personality.

## Systems Before Tweaks

- Constrain choices so decisions are made by elimination, not by endless pixel tweaking.
- Define reusable scales for font size, font weight, line height, color, spacing, sizing, radius, border width, opacity, and shadows.
- Use values that are visually distinct from adjacent values. Tiny differences that no one can see create decision fatigue.
- Use tighter steps at small sizes and larger jumps at large sizes. A 4px jump matters a lot around icons and button padding but barely matters around large panels.
- Introduce a new token only when existing tokens force a bad compromise and the new value will be reused.

## Hierarchy

- Make elements look as important as they are. Interfaces feel noisy when everything competes equally.
- Do not rely only on font size. Use weight, contrast, color, spacing, and placement to create priority.
- Use two font weights for most UI: normal for ordinary text and a heavier weight for emphasis. Avoid very light weights for small UI text.
- On colored backgrounds, do not de-emphasize text by making it generic grey. Pick a lower-contrast color from the same hue family so it looks intentional instead of disabled.
- When something needs more emphasis, first look for competing elements to de-emphasize.
- Treat labels as a last resort for data display. Prefer recognizable formats, contextual grouping, or combined value text such as "12 left in stock" over "In stock: 12".
- When labels are necessary, style labels as supporting content and the data as the focus.
- Separate semantic markup from visual hierarchy. A semantic heading in an app section may need to look small or even be visually hidden if the content carries the meaning.
- Balance visual weight and contrast. Heavy icons often need softer color next to text; subtle borders may need more width instead of harsher color.
- Design actions by importance, not semantics alone. Use strong solid buttons for the true primary action, quieter buttons for secondary actions, and link-like treatments for tertiary actions.
- Do not make destructive actions visually primary unless the destructive action is the current step's main decision, such as inside a confirmation dialog.

## Layout And Spacing

- Start with more whitespace than feels necessary, then reduce it. Adding only the minimum whitespace often leaves the UI cramped.
- Use density deliberately. Dashboards and operational tools can be compact, but compactness should serve scanning and throughput.
- Do not fill the whole screen by default. Give content the width it needs; extra outer space is usually better than stretched content.
- Use fixed widths or `max-width` for elements with an intended reading or interaction size.
- Use grids as a constraint, not a religion. Do not make sidebars, cards, inputs, or components fluid unless scaling actually helps them.
- Do not shrink elements before necessary. Let components keep their ideal size until the viewport forces a compromise.
- Do not encode every relationship as a fixed relative ratio. Large elements usually need to shrink faster than small elements on small screens.
- Ensure grouping is unambiguous. Space inside a group must be smaller than the space around the group.
- For stacked labels and inputs, put labels close to their inputs and leave more space between form groups.
- For articles, lists, cards, and horizontal groups, use spacing to show what belongs together before adding borders.

## Text

- Establish a type scale. Most UI only needs a constrained set of sizes; avoid one-off pixel values.
- Use `px` or `rem` for font-size tokens. Avoid `em` for scale values because nesting creates off-scale computed sizes.
- Use a safe neutral sans-serif for most product UI unless the desired personality needs something else. The system font stack is a reliable default.
- Prefer fonts with enough weights and strong legibility at UI sizes. Avoid condensed faces or short x-heights for body/UI text.
- Keep line length readable. Long-form text usually needs narrower measures than surrounding layout containers.
- Use tighter line-height for headings and larger display text; use more generous line-height for smaller or multi-line body text.
- Align by baseline when mixing text sizes or text with icons where typographic alignment matters.
- Do not center long-form text. Center only short, independent blocks such as headlines, brief calls to action, or small empty-state copy.
- Do not color every link automatically. Use color for links only when discoverability needs it; in dense UI, weight, underline, or context may be cleaner.
- Use letter spacing sparingly. Slight positive tracking can help uppercase microcopy; avoid negative tracking in UI unless the surrounding design system already proves it works.

## Color

- Think in HSL: hue, saturation, and lightness map better to human color decisions than hex alone.
- Build palettes with enough shades. Real interfaces need greys, primary colors, accent/status colors, and multiple shades for each.
- Use 8-10 greys for serious UI work. Start with a very dark grey instead of pure black and include off-white background shades.
- For primary and accent scales, pick a useful base color, then pick darkest and lightest practical endpoints based on real uses such as button backgrounds, text, and tinted alerts.
- Fill shade gaps by eye. Do not generate endless `lighten()` or `darken()` variants on demand.
- Preserve saturation at light and dark extremes. Increase saturation or subtly rotate hue when lightening or darkening so colors do not look washed out.
- Rotate hue in small doses when changing perceived brightness: toward brighter hues for lighter shades and toward darker hues for darker shades.
- Use warm or cool saturated greys to support the product personality instead of defaulting to true neutral grey.
- Accessibility does not require ugly color. If white text on a colored background needs an over-dominant dark background, flip the contrast: use colored text on a light colored background.
- Do not rely on color alone. Add icons, labels, patterns, contrast, or shape so color-blind users can still interpret state and meaning.

## Depth

- Treat light as coming from above. Raised elements can have a subtle lighter top edge and a small darker shadow below.
- Use inset treatments for recessed controls: subtle inner shadow or edge treatment that matches the imagined surface.
- Keep realistic-light details restrained. Over-modeling physical light makes UI busy.
- Use shadows to express elevation, not decoration. Small shadows suit low emphasis; medium shadows suit dropdowns/popovers; large shadows suit modals.
- Create a small elevation system instead of custom shadows everywhere. Five shadow levels is usually enough.
- Consider two-part shadows: a larger soft ambient shadow plus a tighter contact shadow. Fade the contact shadow as elevation increases.
- Flat designs still need depth. Use lighter or darker surfaces, solid offset shadows, and layering even when avoiding blur-heavy shadows.
- Overlap elements when it helps create layers, but preserve readability, hit targets, and responsive behavior.

## Images And Icons

- Use strong photos. Bad photography can make an otherwise solid UI look weak.
- Do not rely on placeholder imagery if final imagery quality is uncertain.
- Text on images needs consistent contrast. Use overlays, lowered image contrast, colorization, or subtle glow-like text shadows to stabilize contrast.
- Respect intended asset size. Do not scale small UI icons into large feature illustrations.
- Put small icons inside larger colored shapes when the layout needs a larger visual mark.
- Do not shrink detailed screenshots until they become unreadable. Capture a smaller viewport, crop to the important area, or simplify the UI representation.
- For favicons and tiny logos, redraw a simplified mark at the target size instead of shrinking a detailed source.
- For user-uploaded images, control shape and crop with fixed containers and cover behavior.
- Prevent background bleed around user images with subtle inner shadow or translucent inner border instead of loud outlines.

## Finishing Details

- Supercharge defaults before adding new decorative elements: custom bullets, stronger quotes, styled links, selected checkbox/radio states, and better default form controls.
- Add color through accent borders when a design needs energy but not a large illustration or photo.
- Use background decoration sparingly: slight color shifts, low-contrast patterns, simple shapes, or small illustrations that never compete with content.
- Keep gradients subtle and close in hue. Wide hue jumps often feel cheap or noisy.
- Prioritize empty states. Hide irrelevant controls when no content exists and give the user a clear next action.
- Use fewer borders. Try shadows, background changes, or extra spacing first.
- Rethink default component shapes. Dropdowns can include sections and supporting text; tables can combine related data; radio options can become selectable cards when the choice matters.
- Study good interfaces by rebuilding them without inspecting code. Look for decisions you would not have made naturally.

## Final Review Checklist

- Does the screen make the main user task obvious before global chrome does?
- Can the hierarchy work in grayscale?
- Are type, spacing, radii, colors, and shadows drawn from a limited system?
- Is the primary action unmistakable, with secondary actions quieter?
- Are labels removed, combined with values, or de-emphasized where possible?
- Is grouping clear from spacing alone?
- Does content use the width it needs instead of filling the viewport?
- Are text sizes, line lengths, line-heights, and alignment readable on desktop and mobile?
- Are palette shades defined up front rather than invented inline?
- Does color meet contrast needs and avoid being the only state signal?
- Do shadows, overlaps, and background treatments communicate depth without clutter?
- Are photos, screenshots, icons, and user-uploaded media displayed at appropriate sizes?
- Are empty, loading, error, and dense-data states designed with the same care as the happy path?
- Are there fewer borders than the first version probably had?
- Has the actual rendered result been checked for overflow, awkward wrapping, overlapping elements, and mobile layout failures?
