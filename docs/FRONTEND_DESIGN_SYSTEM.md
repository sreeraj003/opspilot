# Frontend design system and application shell

Module 05. This is the reference for building OpsPilot AI screens. Components live in
`frontend/src/components/ui/` (import from `@/components/ui/<Name>`, or the barrel `@/components/ui`).

## 1. Design tokens

Tokens are defined once, in `frontend/src/styles/tokens.css`, in two layers:

1. **Raw values per theme** as `--op-*` custom properties on `:root` (light) and `.dark`.
2. **Tailwind theme** (`@theme inline`) mapping semantic names onto them, so components write
   `bg-surface text-fg border-line` and switch theme without any `dark:` variants.

Tailwind's default colour palette is **removed** (`--color-*: initial`). A class such as `bg-blue-500` or
`text-white` produces no CSS, so arbitrary colours cannot creep back into components. Use the names below.

| Group | Utilities (examples) | Use for |
| --- | --- | --- |
| Neutrals | `bg-canvas`, `bg-surface`, `bg-surface-muted`, `bg-surface-strong` | page background, cards, table headers and hovers, tags and pressed states |
| Lines | `border-line`, `border-line-subtle`, `border-line-strong`, `divide-line-subtle` | borders, dividers, control hover |
| Text | `text-fg`, `text-fg-secondary`, `text-fg-muted`, `text-fg-subtle` | headings and values, body, secondary text, icons only (3:1) |
| Accent | `bg-accent`, `bg-accent-hover`, `text-accent-fg`, `bg-accent-subtle`, `text-accent-text`, `ring-ring` | primary actions, active navigation, focus |
| Status | `success`, `warning`, `danger`, `info`, each with `-subtle`, `-text`, `-line` | badges, alerts, toasts, destructive buttons |
| Inverse / overlay | `bg-inverse`, `text-inverse-fg`, `bg-overlay` | tooltips, modal backdrops |

Other tokens:

| Kind | Tokens |
| --- | --- |
| Typography | `font-sans` (Inter stack), `font-mono`; sizes `text-2xs`, `text-page-title` (24/32, semibold), `text-section-title` (16/24, semibold) plus Tailwind's scale; weights normal/medium/semibold/bold |
| Spacing | Tailwind's 0.25 rem scale plus `gutter` (page side padding, 1 rem), `gutter-lg` (2 rem), `section` (1.5 rem between page sections), `sidebar`, `sidebar-collapsed`, `topbar`; `max-w-content` (80 rem) |
| Radius | `rounded-xs`, `rounded-sm`, `rounded-control` (inputs, buttons), `rounded-card`, `rounded-overlay` (dialogs) |
| Shadows | `shadow-xs` (controls), `shadow-card`, `shadow-popover` (menus, toasts), `shadow-overlay` (dialogs) |
| Breakpoints | `sm` 640, `md` 768, `lg` 1024 (sidebar docks), `xl` 1280, `2xl` 1536 px |
| Motion | `ease-standard`, `ease-emphasized`, default transition 150 ms; `animate-fade-in`, `animate-scale-in`, `animate-slide-in-left`, `animate-slide-in-up` |
| Z-index | `--z-sticky` 20, `--z-sidebar` 30, `--z-drawer` 40, `--z-modal` 50, `--z-popover` 60, `--z-toast` 70, `--z-tooltip` 80; use as `z-(--z-modal)` |

`frontend/src/design/tokens.ts` mirrors the values that JavaScript needs (breakpoints, z-index, tooltip and toast
durations, the sidebar dock breakpoint). Keep both files in step when changing a scale.

Contrast (WCAG 2.1 AA) of the main pairs, checked with the WCAG formula: muted text on the canvas 5.4:1 (light) and
7.7:1 (dark); white on the accent 6.3:1; status text on its subtle background 4.75:1 or more. `fg-subtle` (3.2:1)
is for icons and decorative marks only, never for text.

## 2. Themes

- `ThemeProvider` (`features/theme`) holds the preference `light | dark | system` and the resolved theme, toggles the
  `dark` class and `color-scheme` on `<html>`, and follows the operating system live when the preference is `system`.
- The preference (only that word) is stored in `localStorage` under `opspilot.theme`. `public/theme-init.js` applies
  it before the first paint; it is a static file rather than an inline script so a strict Content-Security-Policy
  can allow it.
- `ThemeToggle` (top bar and public pages) and the **Appearance** tab of *Your account* change it.
- Dark mode is a second set of `--op-*` values; components contain no theme logic.
- `prefers-reduced-motion: reduce` shortens every animation and transition globally (`styles/index.css`).

## 3. Components

All components are typed, forward their native props where it makes sense (`ComponentPropsWithRef`, so
`{...register("field")}` from react-hook-form works) and use accessible HTML first.

| Group | Components |
| --- | --- |
| Form | `Button` (variants `primary`, `secondary`, `ghost`, `danger`, `danger-ghost`; sizes `sm`, `md`, `lg`, `icon`, `icon-sm`; `loading`), `ButtonLink`, `buttonClasses()`, `Input`, `Textarea`, `Select` (native), `PasswordInput`, `SearchInput`, `Checkbox`, `Switch`, `FormField`, and the shortcuts `TextField`, `PasswordField`, `TextareaField` |
| Layout | `Card` (optional title/description/actions), `PageHeader`, `SectionHeader`, `Divider`, `Stack`, `ResponsiveContainer` |
| Navigation | `Sidebar` (`layouts/shell/Sidebar.tsx`), `Topbar`, `Breadcrumbs`, `Tabs`, `DropdownMenu` |
| Feedback | `Badge`, `StatusIndicator`, `Alert`, `Toast` (`ToastProvider`, `useToast()`), `Tooltip`, `LoadingSpinner` / `Spinner` / `FullPageSpinner`, `Skeleton` / `SkeletonText`, `EmptyState`, `ErrorState`, `ConfirmDialog` |
| Overlay | `Dialog` (alias `Modal`, with `DialogActions`), `Drawer`, `DropdownMenu`; shared behaviour in `useOverlay.ts` |
| Data | `Table` primitives, `DataTable`, `Pagination`, `Avatar` |

Conventions:

- **Colour only through tokens**; tone maps live in `components/ui/tones.ts`.
- **Variants, not overrides.** Add a variant when a style repeats; pass `className` only for layout (width, margin).
- **Status never by colour alone**: `StatusIndicator` always has a text label.
- **Overlays** (`Dialog`, `Drawer`) move focus in, trap Tab, close on Escape, lock page scroll, render in a portal
  and return focus to the opener. `ConfirmDialog` starts on Cancel and cannot be dismissed while its action runs.
- **Menus** follow the WAI-ARIA menu-button pattern (arrow keys, Home/End, Escape, outside click).
- **Tabs** follow the tabs pattern with automatic activation.
- **Tooltips** appear on hover and focus, are dismissible with Escape and only supplement an accessible name.
- **Toasts** are for short confirmations. Success and info are announced politely and disappear after 5 s (paused
  on hover); errors are announced assertively and stay until dismissed. Use `Alert` for messages that must stay
  in place (form and load errors).
- **Lists** use `DataTable`: skeleton while loading, `ErrorState` with retry, `EmptyState`, `hideBelow` to drop
  secondary columns on small screens, horizontal scrolling inside the card (never the page) and an sr-only caption.

## 4. Forms

React Hook Form with Zod (`@hookform/resolvers`) is the standard:

```tsx
const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues });
<form noValidate onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
  {serverError && <Alert tone="error">{serverError}</Alert>}
  <TextField label="Email" required error={errors.email?.message} {...register("email")} />
  <Button type="submit" loading={isSubmitting}>Save</Button>
</form>
```

- `FormField` links label, description and error to the control (`htmlFor`, `aria-describedby`, `aria-invalid`,
  `aria-required`); the required asterisk is outside the label so it is not read as part of the name.
- Submit buttons use `loading={isSubmitting}`, which disables them while the request runs.
- Server errors are shown with `errorMessage(error)` in an `Alert` (or the mutation's error state).
- Shared rules live with their feature (`features/auth/validation.ts`, `features/users/validation.ts`). They mirror
  the server only for quick feedback; **frontend validation is never a security boundary** and the server remains
  the authority.

## 5. Layouts and routing

| Layout | Route element | Purpose |
| --- | --- | --- |
| Public | `layouts/PublicLayout` | Pages anyone may open, without a session check (`/reset-password`) |
| Authentication | `RedirectIfAuthenticated` → `layouts/AuthLayout` | Sign in and forgotten password; form beside a product panel on large screens |
| Application | `RequireAuth` → `layouts/AppLayout` | The authenticated shell |
| Administration | `layouts/AdminLayout` (under `/admin`) | Shown to users who can open at least one administration page; each page keeps its own `RequirePermission` |

`AppLayout` provides a skip link, the docked sidebar (≥ 1024 px, collapsible to an icon rail, remembered in
`localStorage` as `opspilot.sidebar.collapsed`), a navigation drawer below that width, the sticky top bar
(mobile menu button, page title or breadcrumbs, theme picker, account menu), and the content area inside an
`ErrorBoundary` (reset on navigation) and `Suspense` (administration pages are loaded on demand).
There is no notification area: nothing in the product produces notifications yet.

Sign-out is confirmed (`SignOutDialog`) from the sidebar and the account menu. Session handling is unchanged from
Module 03: the access token stays in memory, the refresh token in the HttpOnly cookie, expired sessions return to
the sign-in page with a notice, and guards never redirect in a loop (`/login` is only shown when signed out).

## 6. Adding a page or navigation item (future modules)

1. Create the page in `src/pages/` using `PageHeader`, `Card`, `DataTable` and friends.
2. Add a route in `src/app/routes.tsx` under the right layout, with a breadcrumb: `handle: crumb("Reports")`.
   For a heavy page, add it to `src/app/lazyPages.ts` and use the lazy export.
3. If it belongs in the sidebar, add an item to `src/app/navigation.ts`:

   ```ts
   { id: "reports", label: "Reports", to: "/reports", icon: BarChart3, permission: "reports:view" }
   ```

   Use `permission` (one key) or `anyPermission` (any of several keys). Use **permission keys, never role names**.
   Add a new section `{ id, label, items }` for a new area. The sidebar, drawer and `AdminLayout` read this list.
4. Wrap the route in `<RequirePermission permission="...">` if it needs one.

Hiding navigation is not authorization: every endpoint must enforce its permission on the server.

## 7. Permissions in the interface

`usePermissions().hasPermission(key)` reads the effective permissions from `GET /api/v1/authorization/me`
(refetched on window focus, every 5 minutes and after any access change made in the app). Until the first answer
the permissions from the sign-in response are used, so navigation appears immediately and then follows the server.
Actions that need `*:manage` are hidden for users without it. Dynamically created permission keys work the same way.
