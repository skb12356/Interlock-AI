export class ResumeTokenVault {
  private readonly tokens = new Map<string, string>();

  store(holdId: string, token: string): void {
    this.tokens.set(holdId, token);
  }

  get(holdId: string): string | undefined {
    return this.tokens.get(holdId);
  }

  delete(holdId: string): void {
    this.tokens.delete(holdId);
  }

  clear(): void {
    this.tokens.clear();
  }
}
