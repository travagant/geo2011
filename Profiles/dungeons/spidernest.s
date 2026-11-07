// Dungeon level 2

id	"spidernest";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon_spider\\arena_back_dungeon_spider_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
unit	"neutrals_giantblackspider"					;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"neutrals_necromancer"					;
unit	"neutrals_zombie"					;
}						
						
party						
{						
unit	"undead_Warlock"					;
unit	"undead_Warlock"					;
unit	"undead_Skeleton"					;
unit	"undead_Skeleton"					;
}						
						
party						
{						
unit	"neutrals_thug"					;
unit	"neutrals_thug"					;
unit	"neutrals_giantblackspider"					;
unit	"empire_mage"					;
}						
						
party						
{						
unit	"undead_Death"					;
}						
						
party						
{						
unit	"neutrals_giantblackspider"					;
	caster	2	"undead"	1-7	7	;
}						
						
party						
{						
unit	"neutrals_primitivegiant"					;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"neutrals_death"					;
unit	"neutrals_death"					;
unit	"undead_Specter"					;
}						
						
party						
{						
	melee		"empire"	1-5	5	;
unit	"neutrals_giantblackspider"					;
unit	"empire_wizard"					;
}						
						
party						
{						
unit	"demons_doppelganger"					;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"undead_Specter"					;
unit	"undead_templar"					;
unit	"undead_templar"					;
}						
						
party						
{						
unit	"undead_Wolf"				7	;
unit	"undead_Wolf"				7	;
unit	"undead_templar"					;
unit	"undead_templar"					;
}						
						
party						
{						
unit	"undead_Deathdragon"					;
}						
						
party						
{						
unit	"empire_witchhunter"					;
unit	"empire_witchhunter"					;
	range		"empire"	1-7	7	;
	healer		"empire"	5-13	13	;
}						
						
party						
{						
	healer		"elves"	1-5	5	;
unit	"elves_hunter"					;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"elves_adept"				5	;
unit	"elves_adept"				5	;
unit	"elves_earthelemental"					;
unit	"elves_earthelemental"					;
}						
						
party						
{						
unit	"demons_sorcerer"				7	;
unit	"demons_sorcerer"				7	;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"empire_inquisitor"					;
unit	"empire_mage"				7	;
unit	"empire_mage"				7	;
}						
						
party						
{						
unit	"empire_inquisitor"					;
unit	"empire_imperialpriestess"					;
}						
						
party						
{						
unit	"neutrals_goblinarcher"					;
unit	"neutrals_goblin"				7	;
unit	"neutrals_goblin"				7	;
unit	"neutrals_giantblackspider"					;
}						
						
party						
{						
unit	"neutrals_goblinslinger"					;
unit	"neutrals_giantblackspider"					;
}						

rewards "dungeon_lvl_2";
