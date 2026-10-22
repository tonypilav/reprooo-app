import { Button, Paper, createTheme } from "@mantine/core";

export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: 6,
  colors: {
    brand: [
      "#fff2ec",
      "#ffe1d4",
      "#fbc3aa",
      "#f6a283",
      "#f1805a",
      "#e06a3f",
      "#d1582c",
      "#b34a24",
      "#953e1e",
      "#783218",
    ],
    dark: [
      "#ece9e6",
      "#c6c2bd",
      "#9e9993",
      "#79746e",
      "#4f4b47",
      "#3a3734",
      "#262422",
      "#1a1918",
      "#141312",
      "#0e0d0d",
    ],
  },
  defaultRadius: "lg",
  headings: { fontWeight: "400" },
  components: {
    Button: Button.extend({ defaultProps: { radius: "xl" } }),
    Paper: Paper.extend({
      defaultProps: { radius: "xl", withBorder: true, p: "xl" },
    }),
  },
});
