#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/task_manager"
SERVICE_USER="taskmgr"
SERVICE_GROUP="taskmgr"
ENV_FILE="$APP_DIR/.env"
UNIT_SOURCE="$APP_DIR/deploy/taskmanager.service"
UNIT_DEST="/etc/systemd/system/taskmanager.service"

fail() {
    printf 'Lỗi: %s\n' "$*" >&2
    exit 1
}

if [[ "$EUID" -ne 0 ]]; then
    fail "Hãy chạy bằng sudo: sudo bash $0"
fi

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$PROJECT_ROOT" != "$APP_DIR" ]]; then
    fail "Hãy chép dự án vào $APP_DIR rồi chạy lại script tại đó."
fi

for required_file in app.py requirements.txt .env.example deploy/taskmanager.service; do
    [[ -f "$APP_DIR/$required_file" ]] || fail "Thiếu tệp $APP_DIR/$required_file."
done

command -v systemctl >/dev/null || fail "Không tìm thấy systemctl; script này dành cho máy Linux dùng systemd."
command -v python3 >/dev/null || fail "Cần cài đặt python3 trước."

if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    install -o root -g "$SERVICE_GROUP" -m 0640 "$APP_DIR/.env.example" "$ENV_FILE"
fi

port_entries="$(grep -Ec '^[[:space:]]*STREAMLIT_SERVER_PORT[[:space:]]*=' "$ENV_FILE" || true)"
if [[ "$port_entries" -ne 1 ]]; then
    fail "$ENV_FILE cần có đúng một dòng STREAMLIT_SERVER_PORT=<cổng>."
fi
port="$(sed -nE 's/^[[:space:]]*STREAMLIT_SERVER_PORT=([0-9]+)[[:space:]]*$/\1/p' "$ENV_FILE")"
if [[ -z "$port" || "${#port}" -gt 5 ]]; then
    fail "STREAMLIT_SERVER_PORT phải là số nguyên từ 1 đến 65535."
fi
port_number=$((10#$port))
if ((port_number < 1 || port_number > 65535)); then
    fail "STREAMLIT_SERVER_PORT phải là số nguyên từ 1 đến 65535."
fi

address_entries="$(grep -Ec '^[[:space:]]*STREAMLIT_SERVER_ADDRESS[[:space:]]*=' "$ENV_FILE" || true)"
if [[ "$address_entries" -eq 0 ]]; then
    printf '\nSTREAMLIT_SERVER_ADDRESS=127.0.0.1\n' >> "$ENV_FILE"
elif [[ "$address_entries" -ne 1 ]]; then
    fail "$ENV_FILE chỉ được có tối đa một dòng STREAMLIT_SERVER_ADDRESS=<địa chỉ>."
fi
address="$(sed -nE 's/^[[:space:]]*STREAMLIT_SERVER_ADDRESS=([[:alnum:].:_-]+)[[:space:]]*$/\1/p' "$ENV_FILE")"
if [[ -z "$address" ]]; then
    fail "STREAMLIT_SERVER_ADDRESS phải là địa chỉ máy chủ hợp lệ, ví dụ 127.0.0.1 hoặc 0.0.0.0."
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$APP_DIR/data" "$APP_DIR/data/backups"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv" || fail "Không tạo được virtualenv; hãy cài gói python3-venv."
fi
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

# Keep application code read-only to the service account; only data is writable.
chown -R root:"$SERVICE_GROUP" "$APP_DIR"
chmod -R go-w "$APP_DIR"
chmod -R g+rX "$APP_DIR"
chown root:"$SERVICE_GROUP" "$ENV_FILE"
chmod 0640 "$ENV_FILE"
chown -R "$SERVICE_USER":"$SERVICE_GROUP" "$APP_DIR/data"
chmod -R u+rwX,go-rwx "$APP_DIR/data"
chmod 0750 "$APP_DIR/data" "$APP_DIR/data/backups"

if [[ -f "$APP_DIR/data/app.db" ]]; then
    TASKS_DB_PATH="$APP_DIR/data/app.db" runuser -u "$SERVICE_USER" -- \
        "$APP_DIR/.venv/bin/python" -m scripts.backup_db --keep 30
fi

install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_DEST"
systemctl daemon-reload
systemctl enable taskmanager
if systemctl is-active --quiet taskmanager; then
    systemctl restart taskmanager
else
    systemctl start taskmanager
fi

if ! systemctl is-active --quiet taskmanager; then
    systemctl status --no-pager taskmanager >&2 || true
    fail "Service taskmanager không khởi động được."
fi

printf 'Đã cài và khởi động service taskmanager.\n'
printf 'Cổng Streamlit: %s (cấu hình trong %s)\n' "$port_number" "$ENV_FILE"
printf 'Địa chỉ lắng nghe: %s\n' "$address"
printf 'Kiểm tra trạng thái: sudo systemctl status taskmanager\n'
printf 'Tạo tài khoản quản trị: sudo -u %s %s/.venv/bin/python -m scripts.create_admin\n' "$SERVICE_USER" "$APP_DIR"
printf 'Nếu lắng nghe trên 0.0.0.0, hãy giới hạn cổng bằng tường lửa cho các máy/mạng tin cậy.\n'
