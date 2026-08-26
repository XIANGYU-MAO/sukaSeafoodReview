export interface LoginName {
  name: string;
}

export interface AuthState {
  id: string;
  name: string;
  role: "reviewer" | "admin";
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
