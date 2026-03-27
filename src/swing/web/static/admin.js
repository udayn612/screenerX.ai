(async function () {
    const errEl = document.getElementById("adminErr");
    const table = document.getElementById("usersTable");
    const body = document.getElementById("usersBody");
    const meta = document.getElementById("adminMeta");

    function showErr(msg) {
        errEl.textContent = msg;
        errEl.hidden = false;
    }

    try {
        const me = await fetch("/api/me", { credentials: "include" }).then((r) => r.json());
        if (!me.auth_configured) {
            showErr("Auth is not enabled on this server.");
            return;
        }
        if (!me.authenticated) {
            window.location.href = "/login";
            return;
        }
        if (!me.is_admin) {
            showErr("You do not have access to the admin panel.");
            meta.textContent = "";
            return;
        }

        const res = await fetch("/api/admin/users", { credentials: "include" });
        if (res.status === 401) {
            window.location.href = "/login";
            return;
        }
        if (res.status === 403) {
            showErr("Admin only.");
            return;
        }
        if (!res.ok) {
            showErr("Could not load users.");
            return;
        }
        const data = await res.json();
        const users = data.users || [];
        meta.textContent = `${users.length} user(s) total.`;

        if (users.length === 0) {
            meta.textContent = "No sign-ins recorded yet.";
            return;
        }

        table.hidden = false;
        body.innerHTML = users
            .map((u) => {
                const pic = u.picture
                    ? `<img class="avatar" src="${escapeHtml(u.picture)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
                    : "";
                return `<tr>
          <td>${pic}</td>
          <td>${escapeHtml(u.email)}</td>
          <td>${escapeHtml(u.name || "—")}</td>
          <td>${escapeHtml(u.first_login_utc || "")}</td>
          <td>${escapeHtml(u.last_login_utc || "")}</td>
          <td>${escapeHtml(String(u.login_count ?? ""))}</td>
        </tr>`;
            })
            .join("");
    } catch (e) {
        showErr("Network error.");
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }
})();
