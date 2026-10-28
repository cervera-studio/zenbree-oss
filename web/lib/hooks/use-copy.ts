'use client';

import { useState } from 'react';

export function useCopy() {
  const [copied, setCopied] = useState(false);

  async function copy(text: string): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      return true;
    } catch {
      return false;
    }
  }

  return { copy, copied, resetCopied: () => setCopied(false) };
}
