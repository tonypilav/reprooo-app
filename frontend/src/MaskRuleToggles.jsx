import { Chip, Group, Stack, Text, Tooltip } from "@mantine/core";
import { t } from "./texts";

function MaskRuleToggles({ rules, value, onChange }) {
  const lockedKeys = rules.filter((rule) => rule.locked).map((rule) => rule.key);

  function handleChange(next) {
    onChange([...new Set([...next, ...lockedKeys])]);
  }

  return (
    <Stack gap="xs">
      <Text size="sm" fw={500}>
        {t.rules.title}
      </Text>
      <Chip.Group multiple value={value} onChange={handleChange}>
        <Group gap="xs">
          {rules.map((rule) => (
            <Tooltip
              key={rule.key}
              label={
                rule.locked
                  ? `${rule.description}. ${t.rules.locked}`
                  : rule.description
              }
              withArrow
              multiline
              w={240}
            >
              <div>
                <Chip value={rule.key} disabled={rule.locked} size="sm">
                  {rule.label}
                </Chip>
              </div>
            </Tooltip>
          ))}
        </Group>
      </Chip.Group>
    </Stack>
  );
}

export default MaskRuleToggles;