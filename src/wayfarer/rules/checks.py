"""Server-owned demo checks."""
import secrets

def roll(target):
    dice = [secrets.randbelow(6) + 1 for _ in range(3)]
    total = sum(dice)
    critical_success = total <= 4 or (total == 5 and target >= 15) or (total == 6 and target >= 16)
    critical_failure = total == 18 or (total == 17 and target <= 15) or total-target >= 10
    success = critical_success or (not critical_failure and total != 17 and total <= target)
    return {'dice': dice, 'total': total, 'target': target, 'success': success,
            'critical': 'success' if critical_success else 'failure' if critical_failure else None}

