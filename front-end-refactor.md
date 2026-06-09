Frontend Refactor Task List
Phase 1 — App Shell and Navigation Upgrade
1.1 Improve desktop sidebar
 Add sidebar section labels:
Main
Records
Documents
Admin
 Add active indicator bar beside the active sidebar item.
 Add icon background highlight on hover.
 Add smooth sidebar hover expansion.
 Add sidebar footer with:
signed-in username
user role
logout link
 Keep sidebar collapsed by default on desktop.
 Make sidebar expand on hover and keyboard focus.
 Ensure active states still use request.endpoint.
1.2 Improve mobile navigation
 Replace simple collapsed mobile navbar with Bootstrap Offcanvas.
 Add mobile menu button in top navbar.
 Add same navigation links inside offcanvas:
Dashboard
Search
Residents
Documents
Reports
Admin links
 Add icons to mobile navigation.
 Add active states to mobile navigation.
 Keep mobile menu accessible with proper labels.
1.3 Add main app header
 Add sticky or semi-sticky content header inside the main area.
 Show current page title.
 Show breadcrumb.
 Add quick search form.
 Add quick action buttons:
Add Resident
Create Draft
Search Records
 Add user action area:
username
role
logout
 Keep existing top branding visible.
1.4 Add breadcrumb component
 Create reusable breadcrumb styling.
 Use breadcrumbs for:
Dashboard
Residents
Documents
Reports
Search
Admin pages
Resident profile
Document history
 Keep breadcrumbs visual-only first.
 Later, connect dynamic breadcrumb labels if needed.
Phase 2 — Global CSS Component System
2.1 Add reusable layout classes
 .bdm-app-shell
 .bdm-sidebar
 .bdm-sidebar-link
 .bdm-sidebar-section
 .bdm-app-header
 .bdm-breadcrumb
 .bdm-content
 .bdm-section
 .bdm-section-header
 .bdm-section-title
 .bdm-section-subtitle
 .bdm-section-actions
2.2 Add reusable card components
 .bdm-hero-card
 .bdm-stat-card
 .bdm-action-card
 .bdm-info-card
 .bdm-profile-card
 .bdm-chart-card
 .bdm-warning-card
 .bdm-admin-card
2.3 Add reusable table components
 .bdm-table-card
 .bdm-table-toolbar
 .bdm-table-title
 .bdm-table-actions
 .bdm-row-actions
 .bdm-empty-row
 .bdm-mobile-record-card
2.4 Add reusable form components
 .bdm-form-card
 .bdm-form-section
 .bdm-form-section-title
 .bdm-form-grid
 .bdm-field-error
 .bdm-form-help
 .bdm-sticky-actions
 .bdm-required
2.5 Add reusable utility components
 .bdm-empty-state
 .bdm-status
 .bdm-status-active
 .bdm-status-archived
 .bdm-status-draft
 .bdm-status-issued
 .bdm-status-warning
 .bdm-filter-chip
 .bdm-filter-chip-remove
 .bdm-timeline
 .bdm-timeline-item
 .bdm-camera-card
2.6 Add animations and effects
 Page entrance fade-in.
 Card hover lift.
 Sidebar expand transition.
 Sidebar label fade-in.
 Button press animation.
 Table row hover.
 Improved focus-visible ring.
 Dropdown polish.
 Alert/toast animation.
Phase 3 — Dashboard Upgrade
3.1 Add dashboard hero panel
 Add welcome message:
Welcome back, {{ current_user.username }}
 Add short dashboard description.
 Add quick buttons:
Add Resident
Create Draft
Search Records
Open Reports
 Add icon or subtle decorative background.
 Make hero responsive on mobile.
3.2 Improve dashboard metric cards
 Add card for total residents.
 Add card for issued documents.
 Add card for documents this month.
 Add card for drafts.
 Add card for archived records.
 Add icons per card.
 Add small action link per card.
 Add optional trend text if backend supports it later.
3.3 Add quick action grid
 Add Resident
 Issue Document
 Search Records
 View Reports
 Manage Document Types
 Create Backup
3.4 Improve chart section
 Wrap each chart in .bdm-chart-card.
 Add consistent chart header.
 Add small helper descriptions.
 Improve chart spacing.
 Keep existing Chart.js variables unchanged.
3.5 Add activity timeline
 Keep existing Recent Activity table.
 Add optional timeline view above or beside it.
 Use audit log data already available.
 Show:
action
user
timestamp
entity
 Keep table as fallback.
Phase 4 — Residents Page Upgrade
4.1 Add residents summary cards

Backend-needed values:

total residents
active residents
archived residents
male count
female count

Tasks:

 Add summary row above filters.
 Add icon per card.
 Add responsive grid.
 Add fallback if values are not provided.
4.2 Improve filter area
 Add search input with icon.
 Group filters inside .bdm-filter-card.
 Add filter summary chips.
 Add clear filters button.
 Keep existing name, gender, sort, and query fields unchanged.
4.3 Add table toolbar
 Show total visible results.
 Move bulk archive button into toolbar.
 Add selected count.
 Add optional export button later.
 Keep checkbox IDs and bulk form intact.
4.4 Improve resident table
 Use status badge classes.
 Improve avatar display.
 Use consistent action dropdown.
 Keep visible primary actions:
New Doc
Edit
 Move secondary actions into More:
View
Archive
Restore where applicable
4.5 Add mobile resident cards
 Hide full table on small screens if needed.
 Add card layout for each resident.
 Show:
name
Barangay ID
status
gender
address
actions
 Keep table for desktop.
Phase 5 — Resident Profile Upgrade
5.1 Add profile hero card
 Large resident photo.
 Full name.
 Barangay ID.
 Status badge.
 Main actions:
Edit
Issue Document
Back to Residents
5.2 Add information grid

Cards or grid items:

 Gender
 Birth date
 Marital status
 Address
 Created date
 Last updated
 Updated by
5.3 Add document summary cards

Backend-needed values:

total documents
draft documents
issued documents
archived documents

Tasks:

 Add document count summary.
 Add small stat cards.
 Link cards to filtered document history if backend supports it.
5.4 Improve Quick Issue panel
 Convert buttons into compact chips or action cards.
 Group many document types cleanly.
 Add empty state if no document types exist.
5.5 Improve document history
 Keep existing table.
 Add timeline-style history option.
 Add status badges.
 Add cleaner action dropdowns.
Phase 6 — Documents Page Upgrade
6.1 Add document summary cards

Backend-needed values:

total documents
drafts
issued
archived
this month

Tasks:

 Add summary cards above filters.
 Use status colors.
 Make cards responsive.
6.2 Improve document filters
 Use icon search input.
 Group filters visually.
 Add active filter chips.
 Keep all query field names unchanged:
q
type
status
from
to
sort
6.3 Improve document table
 Use better status badges.
 Add file readiness indicator:
PDF ready
No generated file
Template missing
 Add clearer action hierarchy:
Draft: Edit, Issue, More
Issued: Print, Revise, More
Archived: Restore
 Keep existing forms and route actions.
6.4 Add mobile document cards
 Show document type.
 Show resident.
 Show issue date.
 Show status.
 Show actions.
 Keep table on desktop.
Phase 7 — Search Page Upgrade
7.1 Add search hero card
 Large search title.
 Main search input.
 Scope dropdown.
 Search button.
 Include archived toggle.
 Keep existing field names.
7.2 Add search result summary
 Residents found.
 Documents found.
 Total results.
 Archived included status.
7.3 Add result tabs

Possible tabs:

All
Residents
Documents

Tasks:

 Use Bootstrap tabs visually.
 Keep server-rendered results.
 Avoid breaking existing filtering behavior.
7.4 Improve result presentation
 Residents results card/table.
 Documents results card/table.
 Better empty state.
 Optional highlighted search terms later.
7.5 Add recent searches later

Backend-needed:

store latest search keywords per user
show recent searches
allow clicking a recent search
Phase 8 — Forms Upgrade
8.1 Resident form sections

Split into:

 Photo Capture
 Basic Information
 Personal Details
 Address
 Actions

Tasks:

 Use .bdm-form-section.
 Keep all fields and IDs unchanged.
 Add sticky action bar.
8.2 Document form sections

Split into:

 Resident
 Document Type
 Details/Purpose
 Issue Date
 Resident Photo
 Actions

Optional new component:

 Document preview side panel.
8.3 Document Type form sections

Split into:

 Basic Information
 Template Upload
 Validity
 Placeholder Rules
 Options
 Actions

Improve:

 Placeholder picker design.
 JSON textarea visual styling.
 Template upload status.
8.4 Official form sections

Split into:

 Official Details
 Signature Upload
 Status
 Actions
8.5 User forms

Improve:

 Add form card.
 Add role badge/selector styling.
 Add password requirement helper block.
 Add validation summary.
Phase 9 — Admin Pages Upgrade
9.1 Add admin overview page later

Backend route needed.

Cards:

 Users
 Document Types
 Officials
 Audit Logs
 Backups
 System Health
9.2 Improve Users page
 Add user role badges.
 Add status indicators if available.
 Add created date formatting.
 Add table toolbar.
 Add better delete confirmation style.
9.3 Improve Document Types page

Add health badges:

 Template uploaded
 Template active
 Photo required
 Custom placeholders
 Missing template warning
9.4 Improve Officials page
 Add signature status badge.
 Add active/inactive badge.
 Add card/table hybrid layout.
 Add empty state.
9.5 Improve Backups page

Add backup dashboard cards:

 Current DB size
 Latest backup
 Backup count
 Total backup storage

Improve restore section:

 Strong warning card.
 Separate upload restore and existing backup restore.
 Add confirmation warning text.
9.6 Improve Audit Logs page

Frontend:

 Add filter card.
 Add timeline option.
 Add log type badges.

Backend-needed filters:

 user
 action type
 entity type
 date from
 date to
Phase 10 — Notifications and Toasts
10.1 Convert flash messages to toast style
 Keep current flash message source.
 Render messages as Bootstrap Toasts.
 Add top-right toast container.
 Auto-dismiss success messages.
 Keep error messages visible longer.
10.2 Add notification center later

Backend-needed notification data:

 pending drafts
 missing templates
 backup overdue
 recent restore
 failed document generation
 residents missing photo

Frontend:

 Add bell icon in app header.
 Add dropdown list.
 Add notification badge count.
Phase 11 — Backend Enhancements to Support the UI

These are optional but very beneficial.

11.1 Dashboard backend data

Add:

 active resident count
 archived resident count
 draft document count
 issued document count
 archived document count
 latest backup info
 pending drafts list
 recent activity list
11.2 Resident profile backend data

Add:

 document summary per resident
 profile completeness percentage
 missing fields list
 resident activity timeline
11.3 Documents backend data

Add:

 document summary counts
 generated file status
 template health status
 failed generation status if applicable
11.4 Admin backend data

Add:

 latest backup date
 backup count
 backup storage total
 document type template health
 user activity summary
 audit log advanced filters
11.5 Search backend data

Add:

 recent searches
 search result counts
 highlighted matches
 saved filters
Recommended Implementation Order
Sprint 1 — Global App Shell
 Add improved sidebar.
 Add mobile offcanvas.
 Add app header.
 Add breadcrumb.
 Add quick search.
 Add quick actions.
 Add sidebar footer user area.
Sprint 2 — Dashboard
 Add hero welcome card.
 Add quick action grid.
 Improve metric cards.
 Add activity timeline.
 Improve chart cards.
Sprint 3 — Residents and Documents
 Add summary cards.
 Add table toolbar.
 Add filter chips.
 Improve badges.
 Improve action dropdowns.
 Add mobile record cards.
Sprint 4 — Forms
 Split resident form into sections.
 Split document form into sections.
 Split document type form into sections.
 Add sticky action bars.
 Improve camera capture cards.
 Improve validation styling.
Sprint 5 — Admin Pages
 Improve Users page.
 Improve Document Types page.
 Improve Officials page.
 Improve Backups page.
 Improve Audit Logs page.
Sprint 6 — Backend-Supported Enhancements
 Add dashboard counts.
 Add document summary counts.
 Add profile completeness.
 Add backup metrics.
 Add audit log filters.
 Add notification data.
Highest Priority Task List

If you want the biggest improvement first, do this:

 App header with breadcrumb, quick search, and quick actions.
 Better sidebar with section labels, footer user area, and active indicator.
 Mobile offcanvas navigation.
 Dashboard hero panel.
 Dashboard quick action cards.
 Improved metric cards with icons.
 Reusable status badge system.
 Reusable empty state component.
 Table toolbar component.
 Filter chip component.
 Residents table toolbar and summary cards.
 Documents table toolbar and summary cards.
 Resident profile hero card.
 Form section cards.
 Sticky form action bar.
