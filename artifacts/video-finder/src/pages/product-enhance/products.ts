export interface Product {
  id: string;
  name: string;
  brand: string;
  price: number;
  mrp: number;
  image: string;
  category: string;
  rating: number;
  description: string;
  features: string[];
  specifications: Record<string, string>;
}

export const products: Product[] = [
  {
    id: "1",
    name: "Prestige PVC 8.0 Veggie Cutter with 3 Stainless Steel Blades",
    brand: "Prestige",
    price: 464,
    mrp: 695,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Prestige+Veggie+Cutter",
    category: "Home & Kitchen",
    rating: 4.2,
    description: "The Prestige PVC 8 Veggie Cutter is a convenient and efficient manual vegetable chopper designed to make everyday food preparation quick and effortless.",
    features: [
      "High-Quality 3-Blade Design",
      "Jumbo Bowl for large quantities",
      "Comfortable Handles",
      "Anti-Skid Base",
      "Durable Pull Cord"
    ],
    specifications: {
      "Brand": "Prestige",
      "Model": "PVC 8",
      "Material": "Plastic with Stainless Steel Blades",
      "Operation": "Manual",
      "Pack Size": "1 Piece"
    }
  },
  {
    id: "2",
    name: "Milton Thermosteel Flask 750ml",
    brand: "Milton",
    price: 549,
    mrp: 899,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Milton+Flask",
    category: "Home & Kitchen",
    rating: 4.5,
    description: "Keep your beverages hot or cold for hours with this premium Thermosteel flask from Milton.",
    features: [
      "24-hour temperature retention",
      "Stainless steel interior",
      "Leak-proof design",
      "Compact and portable"
    ],
    specifications: {
      "Brand": "Milton",
      "Capacity": "750ml",
      "Material": "Stainless Steel",
      "Color": "Silver"
    }
  },
  {
    id: "3",
    name: "Havells HS8150 1500W Hair Dryer",
    brand: "Havells",
    price: 799,
    mrp: 1299,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Havells+Hair+Dryer",
    category: "Electronics",
    rating: 4.3,
    description: "Professional grade hair dryer with powerful motor for quick drying and styling.",
    features: [
      "1500W powerful motor",
      "2 heat settings",
      "Cool shot button",
      "Foldable handle"
    ],
    specifications: {
      "Brand": "Havells",
      "Wattage": "1500W",
      "Weight": "350g",
      "Cord Length": "1.8m"
    }
  },
  {
    id: "4",
    name: "Pigeon Stovekraft Favourite Outer Lid Cooker 5L",
    brand: "Pigeon",
    price: 1299,
    mrp: 1999,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Pigeon+Cooker",
    category: "Home & Kitchen",
    rating: 4.4,
    description: "Premium quality pressure cooker with outer lid design for safe and efficient cooking.",
    features: [
      "5 litre capacity",
      "Outer lid design",
      "Induction compatible",
      "5-year warranty"
    ],
    specifications: {
      "Brand": "Pigeon",
      "Capacity": "5 Litre",
      "Material": "Aluminium",
      "Induction Base": "Yes"
    }
  },
  {
    id: "5",
    name: "Bajaj Rex Mixer Grinder 750W",
    brand: "Bajaj",
    price: 2199,
    mrp: 3499,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Bajaj+Mixer",
    category: "Home & Kitchen",
    rating: 4.1,
    description: "Powerful mixer grinder with 3 stainless steel jars for all your kitchen needs.",
    features: [
      "750W powerful motor",
      "3 stainless steel jars",
      "Multi-purpose blades",
      "2-year warranty"
    ],
    specifications: {
      "Brand": "Bajaj",
      "Wattage": "750W",
      "Jars": "3",
      "Speeds": "3"
    }
  },
  {
    id: "6",
    name: "Syska LED Bulb 12W Pack of 4",
    brand: "Syska",
    price: 399,
    mrp: 599,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Syska+LED+Bulb",
    category: "Electronics",
    rating: 4.6,
    description: "Energy efficient LED bulbs with long lifespan and bright illumination.",
    features: [
      "12W each",
      "6500K daylight",
      "25000 hours lifespan",
      "2-year warranty"
    ],
    specifications: {
      "Brand": "Syska",
      "Wattage": "12W",
      "Color": "Daylight",
      "Pack": "4 pieces"
    }
  },
  {
    id: "7",
    name: "Butterfly Stainless Steel Water Bottle 1L",
    brand: "Butterfly",
    price: 349,
    mrp: 549,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Butterfly+Bottle",
    category: "Home & Kitchen",
    rating: 4.3,
    description: "Durable stainless steel water bottle with double wall insulation.",
    features: [
      "1 litre capacity",
      "Double wall insulation",
      "Leak-proof lid",
      "BPA free"
    ],
    specifications: {
      "Brand": "Butterfly",
      "Capacity": "1 Litre",
      "Material": "Stainless Steel",
      "Insulation": "Double Wall"
    }
  },
  {
    id: "8",
    name: "Prestige Iris 750W Mixer Grinder",
    brand: "Prestige",
    price: 1899,
    mrp: 2799,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Prestige+Mixer",
    category: "Home & Kitchen",
    rating: 4.2,
    description: "Compact and efficient mixer grinder with powerful motor for daily use.",
    features: [
      "750W motor",
      "3 jars included",
      "Anti-skid feet",
      "2-year warranty"
    ],
    specifications: {
      "Brand": "Prestige",
      "Wattage": "750W",
      "Jars": "3",
      "Speeds": "3"
    }
  },
  {
    id: "9",
    name: "Morphy Richards OTG 52L",
    brand: "Morphy Richards",
    price: 8999,
    mrp: 12999,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Morphy+OTG",
    category: "Home & Kitchen",
    rating: 4.5,
    description: "Premium OTG with motorized rotisserie and convection for perfect baking.",
    features: [
      "52 litre capacity",
      "Motorized rotisserie",
      "Convection mode",
      "Temperature control"
    ],
    specifications: {
      "Brand": "Morphy Richards",
      "Capacity": "52 Litre",
      "Wattage": "2000W",
      "Functions": "Bake, Grill, Toast, Rotisserie"
    }
  },
  {
    id: "10",
    name: "Havells Velocity Neo 400mm Table Fan",
    brand: "Havells",
    price: 1599,
    mrp: 2199,
    image: "https://placehold.co/400x400/f3f4f6/374151?text=Havells+Fan",
    category: "Electronics",
    rating: 4.4,
    description: "Powerful table fan with aerodynamic blade design for superior air delivery.",
    features: [
      "400mm blade",
      "3 speed settings",
      "Tilt mechanism",
      "Low noise operation"
    ],
    specifications: {
      "Brand": "Havells",
      "Blade Size": "400mm",
      "Speed": "3",
      "Power": "55W"
    }
  }
];
