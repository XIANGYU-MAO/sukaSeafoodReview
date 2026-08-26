export const FIXED_NAMES = [
  "Hassan",
  "Mao",
  "Xinhui",
  "Wahid",
  "Sharmaa",
  "Yiming",
] as const;

export type FixedName = (typeof FIXED_NAMES)[number];
export type UserRole = "reviewer" | "admin";

export interface LoginName {
  name: FixedName;
}

export interface AuthState {
  id: string;
  name: FixedName;
  role: UserRole;
  must_change_password: boolean;
  csrf_token: string;
}

export interface LoginPayload {
  name: string;
  password: string;
}

export interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIXED_NAME_SET = new Set<string>(FIXED_NAMES);

export function parseAuthState(value: unknown): AuthState {
  if (!isRecord(value)) {
    throw new Error("Invalid authentication response");
  }
  const { id, name, role, must_change_password: mustChangePassword, csrf_token: csrfToken } = value;
  if (
    typeof id !== "string" ||
    !UUID_PATTERN.test(id) ||
    !isFixedName(name) ||
    (role !== "reviewer" && role !== "admin") ||
    role !== expectedRole(name) ||
    typeof mustChangePassword !== "boolean" ||
    typeof csrfToken !== "string" ||
    !csrfToken.trim()
  ) {
    throw new Error("Invalid authentication response");
  }
  return {
    id,
    name,
    role,
    must_change_password: mustChangePassword,
    csrf_token: csrfToken,
  };
}

export function parseLoginNames(value: unknown): readonly FixedName[] {
  if (!Array.isArray(value) || value.length !== FIXED_NAMES.length) {
    throw new Error("Invalid fixed-name response");
  }
  const names = value.map((entry) => {
    if (!isRecord(entry) || !isFixedName(entry.name)) {
      throw new Error("Invalid fixed-name response");
    }
    return entry.name;
  });
  if (new Set(names).size !== FIXED_NAMES.length) {
    throw new Error("Invalid fixed-name response");
  }
  return FIXED_NAMES;
}

function isFixedName(value: unknown): value is FixedName {
  return typeof value === "string" && FIXED_NAME_SET.has(value);
}

function expectedRole(name: FixedName): UserRole {
  return name === "Mao" ? "admin" : "reviewer";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
