export interface PasswordRequirement {
  id: string;
  label: string;
  test: (pw: string) => boolean;
}

export const PASSWORD_REQUIREMENTS: PasswordRequirement[] = [
  { id: "length", label: "At least 8 characters", test: (pw) => pw.length >= 8 },
  { id: "lower", label: "Lowercase letter", test: (pw) => /[a-z]/.test(pw) },
  { id: "upper", label: "Uppercase letter", test: (pw) => /[A-Z]/.test(pw) },
  { id: "digit", label: "Number", test: (pw) => /\d/.test(pw) },
  { id: "special", label: "Special character", test: (pw) => /[^A-Za-z0-9]/.test(pw) },
];

export function passwordMissing(pw: string): string[] {
  return PASSWORD_REQUIREMENTS.filter((r) => !r.test(pw)).map((r) => r.id);
}

export function isPasswordValid(pw: string): boolean {
  return passwordMissing(pw).length === 0;
}
