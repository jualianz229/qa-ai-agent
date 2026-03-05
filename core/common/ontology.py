COMPONENT_ONTOLOGY = {
    "search": {
        "signals": ("search", "cari", "keyword", "find"),
        "actions": ("fill", "click", "assert_text_visible"),
    },
    "filter": {
        "signals": ("filter", "sort", "urut", "kategori", "category"),
        "actions": ("select", "click", "assert_text_visible"),
    },
    "pagination": {
        "signals": ("page", "halaman", "next", "previous", "prev"),
        "actions": ("click", "assert_url_contains"),
    },
    "navigation": {
        "signals": ("menu", "home", "about", "contact", "nav"),
        "actions": ("click", "assert_url_contains"),
    },
    "form": {
        "signals": ("submit", "send", "save", "login", "register", "apply"),
        "actions": ("fill", "select", "click", "assert_text_visible"),
    },
    "table": {
        "signals": ("table", "standings", "stats", "ranking", "klasemen"),
        "actions": ("click", "assert_text_visible"),
    },
    "content": {
        "signals": ("article", "news", "detail", "read", "story"),
        "actions": ("click", "assert_text_visible"),
    },
    "listing": {
        "signals": ("list", "grid", "cards", "results"),
        "actions": ("click", "assert_url_contains"),
    },
    "tabs": {
        "signals": ("tab", "overview", "details", "info"),
        "actions": ("click", "assert_text_visible"),
    },
    "accordion": {
        "signals": ("accordion", "expand", "collapse", "faq"),
        "actions": ("click", "assert_text_visible"),
    },
    "modal": {
        "signals": ("modal", "dialog", "popup", "overlay"),
        "actions": ("click", "assert_text_visible"),
    },
    "breadcrumb": {
        "signals": ("breadcrumb", "home /", "you are here"),
        "actions": ("click", "assert_url_contains"),
    },
    "card": {
        "signals": ("card", "tile", "panel", "item"),
        "actions": ("click", "assert_text_visible"),
    },
    "hero": {
        "signals": ("hero", "banner", "headline"),
        "actions": ("assert_text_visible",),
    },
    "combobox": {
        "signals": ("combobox", "autocomplete", "suggestions", "typeahead"),
        "actions": ("fill", "select", "assert_text_visible"),
    },
    "datepicker": {
        "signals": ("date picker", "calendar", "date", "schedule"),
        "actions": ("fill", "click", "assert_text_visible"),
    },
    "timepicker": {
        "signals": ("time picker", "time", "schedule time"),
        "actions": ("fill", "click", "assert_text_visible"),
    },
    "toast": {
        "signals": ("toast", "snackbar", "notification", "status message"),
        "actions": ("assert_text_visible", "assert_text_not_visible"),
    },
    "drawer": {
        "signals": ("drawer", "side panel", "offcanvas", "sidebar"),
        "actions": ("click", "assert_text_visible"),
    },
    "file_upload": {
        "signals": ("upload", "file", "attachment", "dropzone"),
        "actions": ("upload", "assert_text_visible"),
    },
    "drag_drop": {
        "signals": ("drag", "drop", "sortable", "draggable"),
        "actions": ("click", "assert_text_visible"),
    },
    "rich_text_editor": {
        "signals": ("editor", "rich text", "wysiwyg", "prosemirror", "quill"),
        "actions": ("fill", "assert_text_visible"),
    },
    "infinite_scroll": {
        "signals": ("infinite scroll", "load more", "virtualized", "lazy load"),
        "actions": ("click", "assert_text_visible"),
    },
    "carousel": {
        "signals": ("carousel", "slider", "swiper", "slides"),
        "actions": ("click", "assert_text_visible"),
    },
    "iframe": {
        "signals": ("iframe", "embedded", "widget"),
        "actions": ("click", "assert_text_visible"),
    },
    "shadow_dom": {
        "signals": ("shadow dom", "web component", "custom element"),
        "actions": ("click", "fill", "assert_text_visible"),
    },
    "chart": {
        "signals": ("chart", "graph", "analytics", "dashboard chart"),
        "actions": ("assert_text_visible",),
    },
    "map": {
        "signals": ("map", "location", "leaflet", "mapbox"),
        "actions": ("click", "assert_text_visible"),
    },
    "consent_banner": {
        "signals": ("cookie", "consent", "privacy choices"),
        "actions": ("click", "assert_text_not_visible"),
    },
    "captcha": {
        "signals": ("captcha", "recaptcha", "bot verification"),
        "actions": ("assert_text_visible",),
    },
    "spa_shell": {
        "signals": ("single page app", "app shell", "react", "vue", "next"),
        "actions": ("click", "assert_text_visible"),
    },
    "graphql_surface": {
        "signals": ("graphql", "apollo", "relay"),
        "actions": ("click", "wait_for_text", "assert_text_visible"),
    },
    "otp_verification": {
        "signals": ("otp", "verification code", "one time password", "kode verifikasi"),
        "actions": ("fill", "click", "assert_text_visible"),
    },
    "sso_login": {
        "signals": ("continue with", "sign in with", "single sign-on", "sso"),
        "actions": ("click", "assert_url_contains"),
    },
    "live_feed": {
        "signals": ("live", "ticker", "real-time", "realtime", "updating"),
        "actions": ("wait_for_text", "assert_text_visible"),
    },
}


ACTION_ONTOLOGY = {
    "open_url": {"target_kind": "page"},
    "click": {"target_kind": "control"},
    "fill": {"target_kind": "field"},
    "select": {"target_kind": "field"},
    "upload": {"target_kind": "field"},
    "hover": {"target_kind": "control"},
    "scroll": {"target_kind": "page"},
    "dismiss": {"target_kind": "control"},
    "wait_for_text": {"target_kind": "text"},
    "checkpoint": {"target_kind": "workflow"},
    "inspect": {"target_kind": "page"},
    "assert_text_visible": {"target_kind": "text"},
    "assert_text_not_visible": {"target_kind": "text"},
    "assert_any_text_visible": {"target_kind": "text"},
    "assert_control_text": {"target_kind": "control"},
    "assert_control_visible": {"target_kind": "control"},
    "assert_title_contains": {"target_kind": "title"},
    "assert_url_contains": {"target_kind": "url"},
    "assert_network_seen": {"target_kind": "network"},
    "assert_network_status_ok": {"target_kind": "network"},
    "assert_graphql_ok": {"target_kind": "network"},
    "assert_endpoint_allowlist": {"target_kind": "network"},
    "assert_cross_origin_safe": {"target_kind": "network"},
}


FIELD_ONTOLOGY = {
    "username": {
        "label": "Username",
        "signals": (
            "username", "user name", "userid", "user id", "login id", "login", "account",
            "nickname", "handle", "member id", "user"
        ),
        "autocomplete": ("username",),
        "input_types": ("text", "email"),
        "aliases": ("username", "user name", "login", "account", "user id", "userid"),
    },
    "password": {
        "label": "Password",
        "signals": ("password", "passcode", "passwd", "kata sandi", "pin password"),
        "autocomplete": ("current-password", "new-password"),
        "input_types": ("password",),
        "aliases": ("password", "passcode", "kata sandi", "passwd"),
    },
    "email": {
        "label": "Email",
        "signals": ("email", "e-mail", "mail address", "email address", "surel"),
        "autocomplete": ("email",),
        "input_types": ("email", "text"),
        "aliases": ("email", "email address", "e-mail", "mail"),
    },
    "phone_number": {
        "label": "Phone Number",
        "signals": (
            "phone", "phone number", "mobile", "mobile number", "telephone", "tel",
            "contact number", "contact no", "whatsapp", "wa", "no hp", "nomor hp",
            "nomor telepon", "handphone", "cellphone"
        ),
        "autocomplete": ("tel",),
        "input_types": ("tel", "text", "number"),
        "aliases": (
            "phone", "phone number", "mobile", "mobile number", "telephone", "tel",
            "contact number", "no hp", "nomor hp", "nomor telepon", "whatsapp"
        ),
    },
    "full_name": {
        "label": "Full Name",
        "signals": ("full name", "nama lengkap", "your name", "customer name", "contact name"),
        "autocomplete": ("name",),
        "input_types": ("text",),
        "aliases": ("full name", "name", "nama lengkap", "customer name", "contact name"),
    },
    "first_name": {
        "label": "First Name",
        "signals": ("first name", "given name", "nama depan", "forename"),
        "autocomplete": ("given-name",),
        "input_types": ("text",),
        "aliases": ("first name", "given name", "nama depan", "forename"),
    },
    "last_name": {
        "label": "Last Name",
        "signals": ("last name", "surname", "family name", "nama belakang"),
        "autocomplete": ("family-name",),
        "input_types": ("text",),
        "aliases": ("last name", "surname", "family name", "nama belakang"),
    },
    "search_query": {
        "label": "Search",
        "signals": ("search", "search keyword", "keyword", "query", "find", "cari"),
        "autocomplete": (),
        "input_types": ("search", "text"),
        "aliases": ("search", "keyword", "query", "find", "cari"),
    },
    "message": {
        "label": "Message",
        "signals": ("message", "comment", "comments", "pesan", "deskripsi", "description", "notes", "catatan"),
        "autocomplete": (),
        "input_types": ("text", "textarea"),
        "aliases": ("message", "comment", "pesan", "description", "notes", "catatan"),
    },
    "address_line_1": {
        "label": "Address",
        "signals": ("address", "street", "street address", "alamat", "address line 1", "address1"),
        "autocomplete": ("street-address", "address-line1"),
        "input_types": ("text",),
        "aliases": ("address", "street", "street address", "alamat", "address line 1"),
    },
    "address_line_2": {
        "label": "Address Line 2",
        "signals": ("address line 2", "address2", "apartment", "suite", "unit", "alamat 2"),
        "autocomplete": ("address-line2",),
        "input_types": ("text",),
        "aliases": ("address line 2", "apartment", "suite", "unit", "alamat 2"),
    },
    "city": {
        "label": "City",
        "signals": ("city", "kota", "town"),
        "autocomplete": ("address-level2",),
        "input_types": ("text",),
        "aliases": ("city", "kota", "town"),
    },
    "state_province": {
        "label": "State",
        "signals": ("state", "province", "provinsi", "region", "county"),
        "autocomplete": ("address-level1",),
        "input_types": ("text", "select"),
        "aliases": ("state", "province", "provinsi", "region"),
    },
    "postal_code": {
        "label": "Postal Code",
        "signals": ("postal code", "postcode", "zip", "zip code", "kode pos"),
        "autocomplete": ("postal-code",),
        "input_types": ("text", "number"),
        "aliases": ("postal code", "postcode", "zip", "zip code", "kode pos"),
    },
    "country": {
        "label": "Country",
        "signals": ("country", "negara", "nation"),
        "autocomplete": ("country", "country-name"),
        "input_types": ("text", "select"),
        "aliases": ("country", "negara", "nation"),
    },
    "company": {
        "label": "Company",
        "signals": ("company", "organization", "organisasi", "perusahaan", "business"),
        "autocomplete": ("organization",),
        "input_types": ("text",),
        "aliases": ("company", "organization", "perusahaan", "business"),
    },
    "job_title": {
        "label": "Job Title",
        "signals": ("job title", "position", "jabatan", "role", "occupation"),
        "autocomplete": ("organization-title",),
        "input_types": ("text",),
        "aliases": ("job title", "position", "jabatan", "occupation"),
    },
    "date": {
        "label": "Date",
        "signals": ("date", "tanggal", "schedule date"),
        "autocomplete": ("bday",),
        "input_types": ("date", "text"),
        "aliases": ("date", "tanggal"),
    },
    "date_of_birth": {
        "label": "Date of Birth",
        "signals": ("date of birth", "birth date", "birthday", "dob", "tanggal lahir"),
        "autocomplete": ("bday",),
        "input_types": ("date", "text"),
        "aliases": ("date of birth", "birth date", "birthday", "dob", "tanggal lahir"),
    },
    "otp_code": {
        "label": "OTP Code",
        "signals": ("otp", "verification code", "one time password", "auth code", "pin code", "kode verifikasi"),
        "autocomplete": ("one-time-code",),
        "input_types": ("text", "number", "tel"),
        "aliases": ("otp", "verification code", "auth code", "pin code", "kode verifikasi"),
    },
    "url": {
        "label": "URL",
        "signals": ("url", "website", "site", "link", "web address"),
        "autocomplete": ("url",),
        "input_types": ("url", "text"),
        "aliases": ("url", "website", "site", "link", "web address"),
    },
    "quantity": {
        "label": "Quantity",
        "signals": ("quantity", "qty", "jumlah", "count", "amount"),
        "autocomplete": (),
        "input_types": ("number", "text"),
        "aliases": ("quantity", "qty", "jumlah", "count", "amount"),
    },
    "combobox_selection": {
        "label": "Selection",
        "signals": ("select", "selection", "choose", "option", "category", "typeahead", "autocomplete"),
        "autocomplete": (),
        "input_types": ("text", "search", "select"),
        "aliases": ("selection", "select", "choose", "option", "category", "autocomplete"),
    },
    "rich_text": {
        "label": "Rich Text",
        "signals": ("editor", "content", "body", "rich text", "wysiwyg", "description"),
        "autocomplete": (),
        "input_types": ("text", "textarea"),
        "aliases": ("editor", "body", "content", "rich text", "description"),
    },
    "file_upload": {
        "label": "File Upload",
        "signals": ("upload", "file", "attachment", "resume", "cv", "document", "image"),
        "autocomplete": (),
        "input_types": ("file",),
        "aliases": ("upload", "file", "attachment", "resume", "cv", "document", "image"),
    },
    "time": {
        "label": "Time",
        "signals": ("time", "jam", "hour", "schedule time"),
        "autocomplete": (),
        "input_types": ("time", "text"),
        "aliases": ("time", "jam", "hour"),
    },
    "generic_text": {
        "label": "Text Field",
        "signals": ("input", "field", "text"),
        "autocomplete": (),
        "input_types": ("text", "textarea"),
        "aliases": ("text field", "input field", "field"),
    },
}
