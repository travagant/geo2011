// Dungeon level 2

id	"dungeon_ship";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_ship_01\\arena_back_ship_01_night_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
unit	"neutrals_masterthug"					;
unit	"neutrals_thug"					;
unit	"neutrals_thug"					;
	caster		"empire"	1-5	5	;
}						
						
party						
{						
	healer		"empire"	1-5	5	;
unit	"neutrals_thug"				7	;
unit	"neutrals_thug"				7	;
unit	"elves_hunter"				9	;
}						
						
party						
{						
unit	"neutrals_masterthug"				15	;
unit	"elves_scout"				6	;
unit	"elves_scout"				6	;
}						
						
party						
{						
unit	"neutrals_primitivegiant"					;
unit	"neutrals_thug"					;
unit	"elves_hunter"					;
}						
						
party						
{						
unit	"Undead_Ghost"				3	;
unit	"Undead_Ghost"				3	;
unit	"undead_Shade"					;
}						
						
party						
{						
unit	"Neutrals_Zombie"					;
unit	"Neutrals_Zombie"					;
unit	"Undead_Specter"					;
}						
						
party						
{						
unit	"Neutrals_Death"					;
unit	"Neutrals_Death"					;
}						
						
party						
{						
	melee		"undead"	1-7	7	;
	melee		"undead"	1-7	7	;
unit	"Undead_SkeletonWarrior"					;
}						
						
party						
{						
unit	"Undead_Wraith"					;
	melee		"undead"	1-7	7	;
	melee		"undead"	1-7	7	;
}						
						
party						
{						
unit	"Empire_Livingarmor"					;
unit	"Empire_Livingarmor"					;
unit	"undead_Ghost"				5	;
unit	"undead_Ghost"				5	;
}						
						
party						
{						
unit	"Neutrals_Mermaid"					;
unit	"Neutrals_Mermaid"					;
}						
						
party						
{						
unit	"Neutrals_Blacknaga"					;
}						
						
party						
{						
unit	"neutrals_hydra_female"					;
unit	"neutrals_hydra_male"					;
}						
						
party						
{						
unit	"neutrals_hydra_male"					;
unit	"neutrals_hydra_male"					;
}						

rewards "dungeon_lvl_2";
