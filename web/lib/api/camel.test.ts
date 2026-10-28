import { describe, expectTypeOf, it } from 'vite-plus/test';
import type { Camel } from './camel';

type Wire = {
  credits_remaining: number | null;
  plan_credits_reset_at?: string;
  step_2_done: boolean;
  segments: { employee_range: string | null }[];
  meta: { nested_key: { deep_key: 1 } } | null;
};

describe('Camel', () => {
  it('renames keys deep, through arrays, unions and optional fields', () => {
    expectTypeOf<Camel<Wire>>().toEqualTypeOf<{
      creditsRemaining: number | null;
      planCreditsResetAt?: string;
      step2Done: boolean;
      segments: { employeeRange: string | null }[];
      meta: { nestedKey: { deepKey: 1 } } | null;
    }>();
  });
});
